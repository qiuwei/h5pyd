##############################################################################
# Copyright by The HDF Group.                                                #
# All rights reserved.                                                       #
#                                                                            #
# This file is part of H5Serv (HDF5 REST Server) Service, Libraries and      #
# Utilities.  The full HDF5 REST Server copyright notice, including          #
# terms governing use, modification, and redistribution, is contained in     #
# the file COPYING, which can be found at the root of the source code        #
# distribution tree.  If you do not have access to this file, you may        #
# request a copy from help@hdfgroup.org.                                     #
##############################################################################

from __future__ import absolute_import

import os
import base64
import requests
from requests import ConnectionError
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
import json
import logging

MAX_CACHE_ITEM_SIZE=10000  # max size of an item to put in the cache


class CacheResponse(object):
    """ Wrap a json response in a Requests.Response looking class.
        Note: we don't want to keep a proper requests obj in the cache since it
        would contain refernces to other objects
    """
    def __init__(self, rsp):
        # just save off what we need
        self._text = rsp.text
        self._status_code = rsp.status_code
        self._headers = rsp.headers

    @property
    def text(self):
        return self._text

    @property
    def status_code(self):
        return self._status_code

    @property
    def headers(self):
        return self._headers


class HttpConn:
    """
    Some utility methods based on equivalents in base class.
    TBD: Should refactor these to a common base class
    """
    def __init__(self, domain_name, endpoint=None, username=None, password=None, bucket=None,
            api_key=None, mode='a', use_session=True, use_cache=True, logger=None, retries=3, **kwds):
        self._domain = domain_name
        self._mode = mode
        self._domain_json = None
        self._use_session = use_session
        self._retries = retries
        if use_cache:
            self._cache = {}
            self._objdb = {}
        else:
            self._cache = None
            self._objdb = None
        self._logger = logger
        if logger is None:
            self.log = logging
        else:
            self.log = logging.getLogger(logger)
        self.log.debug("HttpCon.init(omaion: {} use_session: {} use_cache: {} retries: {})".format(domain_name, use_session, use_cache, retries))
        if endpoint is None:
            if "HS_ENDPOINT" in os.environ:
                endpoint = os.environ["HS_ENDPOINT"]
            elif "H5SERV_ENDPOINT" in os.environ:
                endpoint = os.environ["H5SERV_ENDPOINT"]
            else:
                endpoint = "http://127.0.0.1:5000"

        self._endpoint = endpoint

        if username is None:
            if "HS_USERNAME" in os.environ:
                username = os.environ["HS_USERNAME"]
            elif "H5SERV_USERNAME" in os.environ:
                username = os.environ["H5SERV_USERNAME"]
        if isinstance(username, str) and (not username or username.upper() == "NONE"):
            username = None
        self._username = username

        if password is None:
            if "HS_PASSWORD" in os.environ:
                password = os.environ["HS_PASSWORD"]
            elif "H5SERV_PASSWORD" in os.environ:
                password = os.environ["H5SERV_PASSWORD"]
        if isinstance(password, str) and (not password or password.upper() == "NONE"):
            password = None
        self._password = password

        if bucket is None:
            if "HS_BUCKET" in os.environ:
                bucket = os.environ["HS_BUCKET"]
            if isinstance(bucket, str) and (not bucket or bucket.upper() == "NONE"):
                bucket = None
        self._bucket = bucket

        if api_key is None and "HS_API_KEY" in os.environ:
            api_key = os.environ["HS_API_KEY"]
        if isinstance(api_key, str) and (not api_key or api_key.upper() == "NONE"):
            api_key = None
        self._api_key = api_key

        self._s = None  # Sessions


    def getHeaders(self, username=None, password=None, headers=None):
        if headers is None:
            headers = {}
        if username is None:
            username = self._username
        if password is None:
            password = self._password
        if username is not None and password is not None:
            auth_string = username + ':' + password
            auth_string = auth_string.encode('utf-8')
            auth_string = base64.b64encode(auth_string)
            auth_string = b"Basic " + auth_string
            headers['Authorization'] = auth_string
        return headers

    def verifyCert(self):
        # default to validate CERT for https requests, unless
        # the H5PYD_VERIFY_CERT environment variable is set and True
        #
        # TBD: set default to True once the signing authority of data.hdfgroup.org is
        # recognized
        if "H5PYD_VERIFY_CERT" in os.environ:
            verify_cert = os.environ["H5PYD_VERIFY_CERT"].upper()
            if verify_cert.startswith('F'):
                return False
        return True

    def getObjDb(self):
        return self._objdb


    def GET(self, req, format="json", params=None, headers=None, use_cache=True):
        if self._endpoint is None:
            raise IOError("object not initialized")
        #if self._domain is None:
        #    raise IOError("no domain defined")

        if self._objdb:
            pass
            #raise IOError("test extra GET")

        rsp = None

        if not headers:
            headers = self.getHeaders()

        if params is None:
            params = {}
        if "domain" not in params:
            params["domain"] = self._domain
        if "bucket" not in params and self._bucket:
            params["bucket"] = self._bucket
        if self._api_key:
            params["api_key"] = self._api_key
        #print("GET: {} [{}] bucket: {}".format(req, params["domain"], self._bucket))

        if format == "binary":
            headers['accept'] = 'application/octet-stream'

        if self._cache is not None and use_cache and format == "json" and params["domain"] == self._domain:

            self.log.debug("httpcon - checking cache")
            if req in self._cache:
                self.log.debug("httpcon - returning cache result")
                rsp = self._cache[req]
                return rsp

        self.log.info("GET: {} [{}]".format(self._endpoint + req, params["domain"]))
        if self._username and self._password:
            auth = (self._username, self._password)
        else:
            auth = None
        try:
            s = self.session
            rsp = s.get(self._endpoint + req, params=params, headers=headers, auth=auth, verify=self.verifyCert())
            self.log.info("status: {}".format(rsp.status_code))
        except ConnectionError as ce:
            self.log.error("connection error: {}".format(ce))
            raise IOError("Connection Error")
        if rsp.status_code == 200 and self._cache is not None:
            rsp_headers = rsp.headers
            content_length = 0
            self.log.debug("conent_length: {}".format(content_length))
            if "Content-Length" in rsp_headers:
                try:
                    content_length = int(rsp_headers['Content-Length'])
                except ValueError:
                    content_length = MAX_CACHE_ITEM_SIZE + 1
            content_type = None
            if "Content-Type" in rsp_headers:
                content_type = rsp_headers['Content-Type']
            self.log.debug("content_type: {}".format(content_type))

            if content_type.startswith('application/json') and content_length < MAX_CACHE_ITEM_SIZE:
                # add to our _cache
                cache_rsp = CacheResponse(rsp)
                self.log.debug("adding {} to cache".format(req))
                self._cache[req] = cache_rsp

            if rsp.status_code == 200 and req == '/':  # and self._domain_json is None:
                self._domain_json = json.loads(rsp.text)
                self.log.info("got domain json: {}".format(self._domain_json))


        return rsp

    def PUT(self, req, body=None, format="json", params=None, headers=None):
        if self._endpoint is None:
            raise IOError("object not initialized")
        if self._domain is None:
            raise IOError("no domain defined")
        if self._cache is not None:
            # update invalidate everything in cache
            self._cache = {}
        if params:
            self.log.info("PUT params: {}".format(params))
        else:
            params = {}

        if "domain" not in params:
            params["domain"] = self._domain
        if "bucket" not in params and self._bucket:
            params["bucket"] = self._bucket
        if self._api_key:
            params["api_key"] = self._api_key

        # verify the file was open for modification
        if self._mode == 'r':
            raise IOError("Unable to create group (No write intent on file)")

        # try to do a PUT to the domain

        if not headers:
            headers = self.getHeaders()

        if format=="binary":
            headers['Content-Type'] = "application/octet-stream"
            # binary write
            data = body
        else:
            data = json.dumps(body)
        self.log.info("PUT: {} format: {} [{} bytes]".format(req, format, len(data)))
        if self._username and self._password:
            auth = (self._username, self._password)
        else:
            auth = None
        try:
            s = self.session
            rsp = s.put(self._endpoint + req, data=data, headers=headers, params=params, auth=auth, verify=self.verifyCert())
            self.log.info("status: {}".format(rsp.status_code))
        except ConnectionError as ce:
            self.log.error("connection error: {}".format(ce))
            raise IOError("Connection Error")

        if rsp.status_code == 201 and req == '/':
            self.log.info("clearning domain_json cache")
            self._domain_json = None

        return rsp

    def POST(self, req, body=None, format="json", params=None, headers=None):
        if self._endpoint is None:
            raise IOError("object not initialized")
        if self._domain is None:
            raise IOError("no domain defined")
        if self._cache is not None:
            # invalidate cache for updates
            # TBD: handle special case for point selection since that doesn't modify anything
            self._cache = {}

        if params is None:
            params = {}
        if "domain" not in params:
            params["domain"] = self._domain
        if "bucket" not in params and self._bucket:
            params["bucket"] = self._bucket
        if self._api_key:
            params["api_key"] = self._api_key

        # verify we have write intent (unless this is a dataset point selection)
        if req.startswith("/datasets/") and req.endswith("/value"):
            point_sel = True
        else:
            point_sel = False
        if self._mode == 'r' and not point_sel:
            raise IOError("Unable perform request (No write intent on file)")

        # try to do a POST to the domain

        if not headers:
            headers = self.getHeaders()

        if format=="binary":
            # For POST, binary we send and recieve data as binary
            headers['Content-Type'] = "application/octet-stream"
            headers['accept'] = 'application/octet-stream'
            # binary write
            data = body
        else:
            data = json.dumps(body)

        self.log.info("POST: " + req)

        if self._username and self._password:
            auth = (self._username, self._password)
        else:
            auth = None
        try:
            s = self.session
            rsp = s.post(self._endpoint + req, data=data, headers=headers, params=params, auth=auth, verify=self.verifyCert())
        except ConnectionError as ce:
            self.log.warn("connection error: ", ce)
            raise IOError(str(ce))

        return rsp

    def DELETE(self, req, params=None, headers=None):
        if self._endpoint is None:
            raise IOError("object not initialized")
        if self._domain is None:
            raise IOError("no domain defined")
        if self._cache is not None:
            self._cache = {}
        if params is None:
            params = {}
        if "domain" not in params:
            params["domain"] = self._domain
        if "bucket" not in params and self._bucket:
            params["bucket"] = self._bucket
        if self._api_key:
            params["api_key"] = self._api_key

        # verify we have write intent
        if self._mode == 'r':
            raise IOError("Unable perform request (No write intent on file)")

        # try to do a DELETE of the resource

        if not headers:
            headers = self.getHeaders()

        self.log.info("DEL: " + req)
        if self._username and self._password:
            auth = (self._username, self._password)
        else:
            auth = None
        try:
            s = self.session
            rsp = s.delete(self._endpoint + req, headers=headers, params=params, auth=auth, verify=self.verifyCert())
            self.log.info("status: {}".format(rsp.status_code))
        except ConnectionError as ce:
            self.log.error("connection error: {}".format(ce))
            raise IOError("Connection Error")

        if rsp.status_code == 200 and req == '/':
            self.log.info("clearning domain_json cache")
            self._domain_json = None

        return rsp

    @property
    def session(self):
        # create a session object to re-use http connection when possible
        s = requests
        retries=self._retries
        backoff_factor=0.1
        status_forcelist=(500, 502, 503, 504)
        if self._use_session:
            if self._s is None:
                s = requests.Session()

                retry = Retry(
                    total=retries,
                    read=retries,
                    connect=retries,
                    backoff_factor=backoff_factor,
                    status_forcelist=status_forcelist
                )
                adapter = HTTPAdapter(max_retries=retry)
                s.mount('http://', adapter)
                s.mount('https://', adapter)
                self._s = s
            else:
                s = self._s
        return s

    def close(self):
        if self._s:
            self._s.close()
            self._s = None

    @property
    def domain(self):
        return self._domain

    @property
    def username(self):
        return self._username

    @property
    def endpoint(self):
        return self._endpoint

    @property
    def password(self):
        return self._password

    @property
    def mode(self):
        return self._mode

    @property
    def domain_json(self):
        if self._domain_json is None:
            rsp = self.GET('/')
            if rsp.status_code != 200:
                raise IOError(rsp.reason)
            # assume JSON
            self._domain_json = json.loads(rsp.text)
        return self._domain_json

    @property
    def root_uuid(self):
        domain_json = self.domain_json
        if "root" not in domain_json:
            raise IOError("Unexpected response")
        root_uuid = domain_json["root"]
        return root_uuid

    @property
    def modified(self):
        """Last modified time of the domain as a datetime object."""
        domain_json = self.domain_json
        if "lastModified" not in domain_json:
            raise IOError("Unexpected response")
        last_modified = domain_json["lastModified"]
        return last_modified

    @property
    def created(self):
        """Creation time of the domain"""
        domain_json = self.domain_json
        if "created" not in domain_json:
            raise IOError("Unexpected response")
        created = domain_json["created"]
        return created

    @property
    def owner(self):
        """ username of creator of domain"""
        domain_json = self.domain_json
        username = None
        if 'owner' in domain_json:
            # currently this is only available for HSDS
            username = domain_json["owner"]
        return username

    @property
    def logging(self):
        """ return name of logging handler"""
        return self.log
