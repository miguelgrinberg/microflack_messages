#!/usr/bin/env python
import os
os.environ['FLASK_CONFIG'] = 'test'

import mock
import time
import unittest

import requests

from microflack_common.auth import generate_token
from microflack_common.test import FlackTestCase

import app
app.socketio = mock.MagicMock()
from app import app, db, socketio


class MessageTests(FlackTestCase):
    def setUp(self):
        self.ctx = app.app_context()
        self.ctx.push()
        db.drop_all()  # just in case
        db.create_all()
        self.client = app.test_client()

    def tearDown(self):
        db.drop_all()
        self.ctx.pop()

    def test_message(self):
        # create a user and a token
        token = generate_token(1)

        # create a message
        r, s, h = self.post('/api/messages', data={'source': 'hello *world*!'},
                            token_auth=token)
        self.assertEqual(s, 201)
        self.assertEqual(socketio.emit.call_args[0][0], 'updated_model')
        self.assertEqual(socketio.emit.call_args[0][1]['class'], 'Message')
        self.assertEqual(socketio.emit.call_args[0][1]['model']['source'],
                         'hello *world*!')
        url = h['Location']

        # create incomplete message
        r, s, h = self.post('/api/messages', data={'foo': 'hello *world*!'},
                            token_auth=token)
        self.assertEqual(s, 400)

        # get message
        r, s, h = self.get(url, token_auth=token)
        self.assertEqual(s, 200)
        self.assertEqual(r['source'], 'hello *world*!')
        self.assertEqual(r['html'], 'hello <em>world</em>!')

        # modify message
        r, s, h = self.put(url, data={'source': '*hello* world!'},
                           token_auth=token)
        self.assertEqual(s, 204)
        self.assertEqual(socketio.emit.call_args[0][0], 'updated_model')
        self.assertEqual(socketio.emit.call_args[0][1]['class'], 'Message')
        self.assertEqual(socketio.emit.call_args[0][1]['model']['source'],
                         '*hello* world!')

        # check modified message
        r, s, h = self.get(url, token_auth=token)
        self.assertEqual(s, 200)
        self.assertEqual(r['source'], '*hello* world!')
        self.assertEqual(r['html'], '<em>hello</em> world!')

        # create a new message
        with mock.patch('microflack_common.utils.time.time',
                        return_value=int(time.time()) + 5):
            r, s, h = self.post('/api/messages',
                                data={'source': 'bye *world*!'},
                                token_auth=token)
        self.assertEqual(s, 201)

        # get list of messages
        r, s, h = self.get('/api/messages', token_auth=token)
        self.assertEqual(s, 200)
        self.assertEqual(len(r['messages']), 2)
        self.assertEqual(r['messages'][0]['source'], '*hello* world!')
        self.assertEqual(r['messages'][1]['source'], 'bye *world*!')

        # get list of messages since
        r, s, h = self.get(
            '/api/messages?updated_since=' + str(int(time.time()) + 1),
            token_auth=token)
        self.assertEqual(s, 200)
        self.assertEqual(len(r['messages']), 1)
        self.assertEqual(r['messages'][0]['source'], 'bye *world*!')

        # create a second user and token
        token2 = generate_token(2)

        # modify message from first user with second user's token
        r, s, h = self.put(url, data={'source': '*hello* world!'},
                           token_auth=token2)
        self.assertEqual(s, 403)

        def responses():
            rv = requests.Response()
            rv.status_code = 200
            rv.encoding = 'utf-8'
            rv._content = (b'<html><head><title>foo</title>'
                           b'<meta name="blah" content="blah">'
                           b'<meta name="description" content="foo descr">'
                           b'</head></html>')
            yield rv
            rv = requests.Response()
            rv.status_code = 200
            rv.encoding = 'utf-8'
            rv._content = b'<html><head><title>bar</title></head></html>'
            yield rv
            rv = requests.Response()
            rv.status_code = 200
            rv.encoding = 'utf-8'
            rv._content = (b'<html><head>'
                           b'<meta name="description" content="baz descr">'
                           b'</head></html>')
            yield rv
            yield requests.exceptions.ConnectionError()

        with mock.patch('app.requests.get', side_effect=responses()):
            r, s, h = self.post(
                '/api/messages',
                data={'source': 'hello http://foo.com!'},
                token_auth=token)
            self.assertEqual(s, 201)
            url = h['Location']
            r, s, h = self.get(url, token_auth=token)
            self.assertEqual(s, 200)
            self.assertEqual(
                r['html'],
                'hello <a href="http://foo.com" rel="nofollow">'
                'http://foo.com</a>!<blockquote><p><a href="http://foo.com">'
                'foo</a></p><p>foo descr</p></blockquote>')

            r, s, h = self.post(
                '/api/messages',
                data={'source': 'hello http://foo.com!'},
                token_auth=token)
            self.assertEqual(s, 201)
            url = h['Location']
            r, s, h = self.get(url, token_auth=token)
            self.assertEqual(s, 200)
            self.assertEqual(
                r['html'],
                'hello <a href="http://foo.com" rel="nofollow">'
                'http://foo.com</a>!<blockquote><p><a href="http://foo.com">'
                'bar</a></p><p>No description found.</p></blockquote>')

            r, s, h = self.post(
                '/api/messages',
                data={'source': 'hello foo.com!'},
                token_auth=token)
            self.assertEqual(s, 201)
            url = h['Location']
            r, s, h = self.get(url, token_auth=token)
            self.assertEqual(s, 200)
            self.assertEqual(
                r['html'],
                'hello <a href="http://foo.com" rel="nofollow">'
                'foo.com</a>!<blockquote><p><a href="http://foo.com">'
                'http://foo.com</a></p><p>baz descr</p></blockquote>')

            r, s, h = self.post(
                '/api/messages',
                data={'source': 'hello foo.com!'},
                token_auth=token)
            self.assertEqual(s, 201)
            url = h['Location']
            r, s, h = self.get(url, token_auth=token)
            self.assertEqual(s, 200)
            self.assertEqual(
                r['html'],
                'hello <a href="http://foo.com" rel="nofollow">'
                'foo.com</a>!')


if __name__ == '__main__':
    unittest.main(verbosity=2)
