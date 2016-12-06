from __future__ import absolute_import

import logging

from requests.exceptions import ConnectionError, RequestException
from sentry.http import build_session
from sentry.utils import json
from sentry.utils.cache import cache
from simplejson.decoder import JSONDecodeError
from BeautifulSoup import BeautifulStoneSoup
from django.utils.datastructures import SortedDict

log = logging.getLogger(__name__)

CACHE_KEY = "SENTRY-JIRA-%s-%s"


class JIRAError(Exception):
    status_code = None

    def __init__(self, response_text, status_code=None):
        if status_code is not None:
            self.status_code = status_code
        self.text = response_text
        self.xml = None
        if response_text:
            try:
                self.json = json.loads(response_text, object_pairs_hook=SortedDict)
            except (JSONDecodeError, ValueError):
                if self.text[:5] == "<?xml":
                    # perhaps it's XML?
                    self.xml = BeautifulStoneSoup(self.text)
                # must be an awful code.
                self.json = None
        else:
            self.json = None
        super(JIRAError, self).__init__(response_text[:128])

    @classmethod
    def from_response(cls, response):
        return cls(response.text, response.status_code)


class JIRAUnauthorized(JIRAError):
    status_code = 401


class JIRAResponse(object):
    """
    A Slimy little wrapper around a python-requests response object that renders
    JSON from JIRA's ordered dicts (fields come back in order, but python obv.
    doesn't care)
    """
    def __init__(self, response_text, status_code):
        self.text = response_text
        self.xml = None
        if response_text:
            try:
                self.json = json.loads(response_text, object_pairs_hook=SortedDict)
            except (JSONDecodeError, ValueError):
                if self.text[:5] == "<?xml":
                    # perhaps it's XML?
                    self.xml = BeautifulStoneSoup(self.text)
                # must be an awful code.
                self.json = None
        else:
            self.json = None
        self.status_code = status_code

    def __repr__(self):
        return "<JIRAResponse<%s> %s>" % (self.status_code, self.text[:120])

    @classmethod
    def from_response(cls, response):
        return cls(response.text, response.status_code)


class JIRAClient(object):
    """
    The JIRA API Client, so you don't have to.
    """

    AUTH_URL = '/rest/auth/1/session'
    PROJECT_URL = '/rest/api/2/project'
    META_URL = '/rest/api/2/issue/createmeta'
    CREATE_URL = '/rest/api/2/issue'
    PRIORITIES_URL = '/rest/api/2/priority'
    VERSIONS_URL = '/rest/api/2/project/%s/versions'
    USERS_URL = '/rest/api/2/user/assignable/search'
    ISSUE_URL = '/rest/api/2/issue/%s'
    HTTP_TIMEOUT = 5

    def __init__(self, instance_uri, username, password):
        self.instance_url = instance_uri.rstrip('/')
        self.username = username
        self.password = password

    def get_projects_list(self):
        return self.get_cached(self.PROJECT_URL)

    def get_create_meta(self, project):
        return self.make_request('get', self.META_URL, {'projectKeys': project, 'expand': 'projects.issuetypes.fields'})

    def get_create_meta_for_project(self, project):
        response = self.get_create_meta(project)
        metas = response.json

        # We saw an empty JSON response come back from the API :(
        if not metas:
            return None

        # XXX(dcramer): document how this is possible, if it even is
        if len(metas["projects"]) > 1:
            raise JIRAError("More than one project found.")

        try:
            return metas["projects"][0]
        except IndexError:
            return None

    def get_versions(self, project):
        return self.get_cached(self.VERSIONS_URL % project)

    def get_priorities(self):
        return self.get_cached(self.PRIORITIES_URL)

    def get_users_for_project(self, project):
        return self.make_request('get', self.USERS_URL, {'project': project})

    def create_issue(self, raw_form_data):
        data = {'fields': raw_form_data}
        return self.make_request('post', self.CREATE_URL, payload=data)

    def get_issue(self, key):
        return self.make_request('get', self.ISSUE_URL % key)

    def get_session(self):
        """
        If a session does not yet exist, build a session and authenticate
        with Jira; otherwise, reuse session
        """
        if not hasattr(self, '_session'):
            self._session = build_session()
            url = self.instance_url + self.AUTH_URL
            res = self._session.post(self.instance_url + self.AUTH_URL,
                                     json=dict(username=self.username,
                                               password=self.password))
            if not res:
                if res.status_code == 401:
                    raise JIRAUnauthorized.from_response(res)
                elif res.status_code >= 500:
                    logging.error('Error in request to %s: %s', url,
                                  res, exc_info=True)
                    raise JIRAError('Internal Error')
                raise JIRAError.from_response(res)
        return self._session

    def make_request(self, method, url, payload=None):
        if url[:4] != "http":
            url = self.instance_url + url
        try:
            session = self.get_session()
            if method == 'get':
                r = session.get(
                    url, params=payload, verify=False,
                    timeout=self.HTTP_TIMEOUT)
            else:
                r = session.post(
                    url, json=payload, verify=False,
                    timeout=self.HTTP_TIMEOUT)
        except ConnectionError as e:
            raise JIRAError(unicode(e))
        except RequestException as e:
            resp = e.response
            if not resp:
                raise JIRAError('Internal Error')
            if resp.status_code == 401:
                raise JIRAUnauthorized.from_response(resp)
            raise JIRAError.from_response(resp)
        except Exception as e:
            logging.error('Error in request to %s: %s', url, e.message[:128],
                          exc_info=True)
            raise JIRAError('Internal error', 500)

        if r.status_code == 401:
            raise JIRAUnauthorized.from_response(r)
        elif r.status_code < 200 or r.status_code >= 300:
            raise JIRAError.from_response(r)
        return JIRAResponse.from_response(r)

    def get_cached(self, full_url):
        """
        Basic Caching mechanism for requests and responses. It only caches responses
        based on URL
        TODO: Implement GET attr in cache as well. (see self.create_meta for example)
        """
        key = CACHE_KEY % (full_url, self.instance_url)
        cached_result = cache.get(key)
        if not cached_result:
            cached_result = self.make_request('get', full_url)
            cache.set(key, cached_result, 60)
        return cached_result
