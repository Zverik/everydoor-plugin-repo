from functools import wraps
from authlib.integrations.flask_client import OAuth
from sqlalchemy.exc import NoResultFound
from flask import (
    Blueprint, request, url_for, redirect, session, g, flash,
)
from .database import db, User


oauth = OAuth()
bp = Blueprint('auth', __name__)


def init_app(app):
    oauth.register(
        'openstreetmap',
        api_base_url='https://api.openstreetmap.org/api/0.6/',
        access_token_url='https://www.openstreetmap.org/oauth2/token',
        authorize_url='https://www.openstreetmap.org/oauth2/authorize',
        client_id=app.config['OAUTH_ID'],
        client_secret=app.config['OAUTH_SECRET'],
        client_kwargs={'scope': 'read_prefs'},
    )
    oauth.init_app(app)


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login', next=request.url))
        return f(*args, **kwargs)
    return decorated


def get_user(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user' not in g:
            g.user = None
            if 'user_id' in session:
                try:
                    g.user = db.session.get_one(User, session['user_id'])
                except NoResultFound:
                    flash('Error: no user in the database. Please re-login')
                    del session['user_id']
                    return redirect(url_for('plugins.list'))
        return f(*args, **kwargs)
    return decorated


@bp.route('/login')
def login():
    url = url_for('auth.authorize', _external=True)
    session['next'] = request.args.get('next', '')
    return oauth.openstreetmap.authorize_redirect(url)


@bp.route('/auth')
def authorize():
    oauth.openstreetmap.authorize_access_token()
    resp = oauth.openstreetmap.get('user/details.json')
    resp.raise_for_status()
    profile = resp.json()
    user_id = profile['user']['id']

    try:
        g.user = db.session.get_one(User, user_id)
    except NoResultFound:
        # Create a new user
        user = User(
            osm_id=user_id,
            name=profile['user']['display_name'],
        )
        user.update_token()
        db.session.add(user)
        db.session.commit()
        g.user = user

    session['user_id'] = user_id
    nxt = session.pop('next')
    return redirect(nxt or url_for('plugins.list'))


@bp.route('/logout')
def logout():
    if 'user_id' in session:
        del session['user_id']
    return redirect(request.args.get('next', url_for('plugins.list')))
