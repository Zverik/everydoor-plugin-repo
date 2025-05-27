import os
import re
from datetime import datetime
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_migrate import Migrate
from markupsafe import escape, Markup


def markdown_format(s: str) -> str:
    return Markup('<br>'.join(re.split(r'(?:\r\n|\r|\n)', escape(s))))


def wtforms_error_class(field):
    return field(class_='form-control' if not field.errors
                 else 'form-control is-invalid')


def date_ago(dt: datetime) -> str:
    days = datetime.now() - dt
    if days.days < 30:
        return f'{days.days} days ago'
    if days.days < 360:
        return f'{days.days // 30} months ago'
    return dt.strftime('%b %Y')


def create_app():
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_mapping(
        SECRET_KEY='sdfsdfsdf',
        SQLALCHEMY_DATABASE_URI='sqlite:///edpr.sqlite',
        OAUTH_ID='',
        OAUTH_SECRET='',
        PROXY=False,
        MAX_UPLOAD_SIZE_MB=25,
    )
    app.config.from_pyfile('config.py', silent=True)
    app.config['MAX_CONTENT_LENGTH'] = (
        app.config['MAX_UPLOAD_SIZE_MB'] * 1024 * 1024)
    os.makedirs(app.instance_path, exist_ok=True)

    from .database import db
    db.init_app(app)
    Migrate(app, db)
    app.add_template_filter(markdown_format, 'markdown')
    app.add_template_filter(wtforms_error_class, 'fc')
    app.add_template_filter(date_ago, 'ago')
    from . import plugins
    app.register_blueprint(plugins.bp)
    from . import api
    app.register_blueprint(api.bp, url_prefix='/api')
    from . import auth
    auth.init_app(app)
    app.register_blueprint(auth.bp)

    if app.config['PROXY']:
        app.wsgi_app = ProxyFix(
            app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    return app
