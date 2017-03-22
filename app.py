import os

import bleach
from bs4 import BeautifulSoup
from flask import Flask, jsonify, request, abort, g
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_socketio import SocketIO
from markdown import markdown
import requests
import threading

import config
from microflack_common.auth import token_auth, token_optional_auth
from microflack_common.utils import timestamp, url_for

app = Flask(__name__)
config_name = os.environ.get('FLASK_CONFIG', 'dev')
app.config.from_object(getattr(config, config_name.title() + 'Config'))

db = SQLAlchemy(app)
migrate = Migrate(app, db)

message_queue = 'redis://' + os.environ['REDIS'] if 'REDIS' in os.environ \
    else None
if message_queue:
    socketio = SocketIO(message_queue=message_queue)
else:
    socketio = None


class Message(db.Model):
    """The Message model."""
    __tablename__ = 'messages'
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.Integer, default=timestamp)
    updated_at = db.Column(db.Integer, default=timestamp, onupdate=timestamp)
    source = db.Column(db.Text, nullable=False)
    html = db.Column(db.Text, nullable=False)
    user_id = db.Column(db.Integer, nullable=False)

    def from_dict(self, data, partial_update=True):
        """Import message data from a dictionary."""
        for field in ['source']:
            try:
                setattr(self, field, data[field])
            except KeyError:
                if not partial_update:
                    abort(400)

    def to_dict(self):
        """Export message to a dictionary."""
        return {
            'id': self.id,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
            'source': self.source,
            'html': self.html,
            'user_id': self.user_id,
            '_links': {
                'self': url_for('get_message', id=self.id),
                'user': '/users/{}'.format(self.user_id)
            }
        }

    def render_markdown(self):
        """Render markdown source to HTML with a tag whitelist."""
        allowed_tags = ['a', 'abbr', 'acronym', 'b', 'code', 'em', 'i',
                        'strong']
        self.html = bleach.linkify(bleach.clean(
            markdown(self.source, output_format='html'),
            tags=allowed_tags, strip=True))

    def expand_links(self):
        """Expand any links referenced in the message."""
        if '<blockquote>' in self.html:
            # links have been already expanded
            return False
        changed = False
        for link in BeautifulSoup(self.html, 'html5lib').select('a'):
            url = link.get('href', '')
            try:
                rv = requests.get(url)
            except requests.exceptions.ConnectionError:
                continue
            if rv.status_code == 200:
                soup = BeautifulSoup(rv.text, 'html5lib')
                title_tags = soup.select('title')
                if len(title_tags) > 0:
                    title = title_tags[0].string.strip()
                else:
                    title = url
                description = 'No description found.'
                for meta in soup.select('meta'):
                    if meta.get('name', '').lower() == 'description':
                        description = meta.get('content', description).strip()
                        break
                # add the detail of the link to the rendered message
                tpl = ('<blockquote><p><a href="{url}">{title}</a></p>'
                       '<p>{desc}</p></blockquote>')
                self.html += tpl.format(url=url, title=title, desc=description)
                changed = True
        return changed


@db.event.listens_for(Message, 'after_update')
def after_user_update(mapper, connection, target):
    if socketio:
        socketio.emit('updated_model', {'class': target.__class__.__name__,
                                        'model': target.to_dict()})


def render_message(id):
    with app.app_context():
        msg = Message.query.get(id)
        if not msg:
            return
        msg.render_markdown()
        db.session.commit()  # first update with rendered markdown
        if (msg.expand_links()):
            db.session.commit()  # final update with expanded links


@app.route('/api/messages', methods=['POST'])
@token_auth.login_required
def new_message():
    """
    Post a new message.
    This endpoint is requires a valid user token.
    """
    msg = Message(user_id=g.jwt_claims['user_id'])
    msg.from_dict(request.get_json(), partial_update=False)
    msg.html = '...'
    db.session.add(msg)
    db.session.commit()
    r = jsonify(msg.to_dict())
    r.status_code = 201
    r.headers['Location'] = url_for('get_message', id=msg.id)

    # render the markdown and expand the links in a background task
    if app.config['TESTING']:
        # for unit tests, render synchronously
        render_message(msg.id)
    else:
        # asynchronous rendering
        render_thread = threading.Thread(target=render_message,
                                         args=(msg.id,))
        render_thread.start()
    return r


@app.route('/api/messages', methods=['GET'])
@token_optional_auth.login_required
def get_messages():
    """
    Return list of messages.
    This endpoint is publicly available, but if the client has a token it
    should send it, as that indicates to the server that the user is online.
    """
    since = int(request.args.get('updated_since', '0'))
    day_ago = timestamp() - 24 * 60 * 60
    if since < day_ago:
        # do not return more than a day worth of messages
        since = day_ago
    msgs = Message.query.filter(Message.updated_at >= since).order_by(
        Message.updated_at)
    return jsonify({'messages': [msg.to_dict() for msg in msgs.all()]})


@app.route('/api/messages/<id>', methods=['GET'])
@token_optional_auth.login_required
def get_message(id):
    """
    Return a message.
    This endpoint is publicly available, but if the client has a token it
    should send it, as that indicates to the server that the user is online.
    """
    return jsonify(Message.query.get_or_404(id).to_dict())


@app.route('/api/messages/<id>', methods=['PUT'])
@token_auth.login_required
def edit_message(id):
    """
    Modify an existing message.
    This endpoint is requires a valid user token.
    Note: users are only allowed to modify their own messages.
    """
    msg = Message.query.get_or_404(id)
    if msg.user_id != g.jwt_claims.get('user_id'):
        abort(403)
    msg.from_dict(request.get_json() or {})
    db.session.add(msg)
    db.session.commit()

    # render the markdown and expand the links in a background task
    if app.config['TESTING']:
        # for unit tests, render synchronously
        render_message(msg.id)
    else:
        # asynchronous rendering
        render_thread = threading.Thread(target=render_message,
                                         args=(msg.id,))
        render_thread.start()
    return '', 204


if __name__ == '__main__':
    app.run()
