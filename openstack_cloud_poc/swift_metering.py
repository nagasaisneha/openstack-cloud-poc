"""Deny-only WSGI restriction for the dedicated scoped billing collector.

Install after Keystone authtoken and before keystoneauth in Swift's pipeline.
The operator role authorizes account HEAD; this restriction permits only the
validated token's own account HEAD, with no object/container reads or writes.
"""
import re


class CollectorAccountHeadOnly:
    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        roles = {item.strip() for item in environ.get('HTTP_X_ROLES', '').split(',')}
        if 'billing_collector' in roles:
            project = environ.get('HTTP_X_PROJECT_ID', '')
            permitted = (environ.get('HTTP_X_IDENTITY_STATUS') == 'Confirmed'
                         and re.fullmatch(r'[0-9a-f]{32}', project) is not None
                         and environ.get('REQUEST_METHOD') == 'HEAD'
                         and environ.get('PATH_INFO') in ('/v1/AUTH_' + project, '/v1/AUTH_' + project + '/'))
            if not permitted:
                start_response('403 Forbidden', [('Content-Type', 'text/plain'), ('Content-Length', '0')])
                return [b'']
        return self.app(environ, start_response)


def filter_factory(global_conf, **local_conf):
    def factory(app):
        guarded = CollectorAccountHeadOnly(app)
        if local_conf.get('byte_journal_path'):
            from .swift_usage_journal import wrap
            return wrap(guarded, local_conf['byte_journal_path'], local_conf['meter_projects_path'])
        return guarded
    return factory
