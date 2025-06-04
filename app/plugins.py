import zipfile
import yaml
import re
import os
import os.path
import json
import qrcode
import qrcode.image.svg
from flask import (
    Blueprint, url_for, redirect, render_template, g,
    current_app, flash, request, abort, send_file,
)
from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired
from wtforms.validators import ValidationError
import wtforms.fields as wtf
import wtforms.validators as wtv
from typing import BinaryIO
from sqlalchemy import func
from sqlalchemy.exc import NoResultFound
from .auth import login_required, get_user
from .database import db, Plugin, PluginVersion
from importlib.resources import read_text


bp = Blueprint('plugins', __name__)
countries = json.loads(read_text('app', 'countries.json'))
FORBIDDEN_NAMES = [
    'my', 'search', 'nav', 'upload', 'edit', 'delete', 'icon',
    'login', 'auth', 'logout', 'api',
]


@bp.route('/', endpoint='list')
@get_user
def plugins_list():
    plugins = db.session.scalars(db.select(Plugin).order_by(Plugin.title))
    plugins = [p for p in plugins if p.last_version]
    return render_template('plugins.html', plugins=plugins, mine=False)


@bp.route('/my', endpoint='my')
@get_user
@login_required
def plugins_mine():
    plugins = db.session.scalars(db.select(Plugin).where(
        Plugin.created_by == g.user).order_by(Plugin.title))
    return render_template('plugins.html', plugins=plugins, mine=True)


@bp.route('/search')
@get_user
def search():
    value = request.args.get('q', '').strip()
    if not value:
        return plugins_list()
    plugins = db.session.scalars(db.select(Plugin).where(
        Plugin.title.like(f'%{value}%')).order_by(Plugin.title))
    return render_template('plugins.html', plugins=plugins,
                           mine=False, search=value)


def validate_country(form, field):
    data = field.data
    if not data:
        return
    if data in countries:
        return
    raise ValidationError(f'{data} is not a correct country identifier')


class UploadForm(FlaskForm):
    package = FileField('EDP Package', validators=[FileRequired()])


def unpack_edp(package: BinaryIO) -> dict:
    maxsize = current_app.config['MAX_CONTENT_LENGTH']
    package_size = package.seek(0, 2)
    package.seek(0)
    if package_size > maxsize:
        raise ValidationError('File is too big')

    try:
        pkg = zipfile.ZipFile(package)
    except Exception as e:
        raise ValidationError(f'Cannot unzip the package: {e}')

    try:
        test_result = pkg.testzip()
        if test_result:
            try:
                raise ValidationError(f'Bad zip file: {test_result}')
            except UnicodeDecodeError:
                raise ValidationError('Bad zip file, maybe charset issue')

        namelist = pkg.namelist()
        for name in namelist:
            if '..' in name:
                raise ValidationError(
                    f'Found "{name}" in zip file, which is wrong')

        if 'plugin.yaml' not in namelist:
            raise ValidationError('Missing plugin.yaml file')

        try:
            content = pkg.read('plugin.yaml').decode('utf8')
            metadata = yaml.safe_load(content)
        except Exception as e:
            raise ValidationError(
                f'Error loading plugin.yaml, must be broken: {e}')

        if not isinstance(metadata, dict):
            raise ValidationError('plugin.yaml should contain a dictionary')

        if 'icon' in metadata:
            icon_file = f'icons/{metadata["icon"]}'
            if icon_file not in namelist:
                raise ValidationError(f'Missing "{icon_file}" file')
            m_ext = re.search(r'\.(svg|gif|png|webp)$', icon_file)
            if not m_ext:
                raise ValidationError(
                    'Icon should be an svg, png, gif, or webp')
            max_icon_size = current_app.config['MAX_ICON_SIZE_KB'] * 1024
            icon_info = pkg.getinfo(icon_file)
            if icon_info.file_size > max_icon_size:
                raise ValidationError(
                    f'Icon file is larger than {max_icon_size}')
            metadata['icon_ext'] = m_ext.group(1)
            metadata['icon_data'] = pkg.read(icon_file)
            if len(metadata['icon_data']) > max_icon_size:
                raise ValidationError(
                    f'Icon file real size is larger than {max_icon_size}')
    finally:
        pkg.close()

    req_keys = ('id', 'name', 'version', 'description')
    for k in req_keys:
        if k not in metadata:
            raise ValidationError(f'Key "{k}" is missing in the metadata')

    if not re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_-]+$', metadata['id']):
        raise ValidationError(
            'Plugin id must be of latin letters, numbers, '
            'dashes, or underscores.')

    if metadata['id'] in FORBIDDEN_NAMES:
        raise ValidationError(
            f'Plugin id is a reserved word: {metadata["id"]}')

    return metadata


@bp.route('/upload', methods=['GET', 'POST'])
@get_user
@login_required
def upload():
    form = UploadForm()
    if form.validate_on_submit():
        # Uploading a package, finally
        try:
            metadata = unpack_edp(form.package.data)

            plugin_id = metadata['id']
            version = PluginVersion.parse_version(metadata['version'])
            if db.session.scalar(
                db.select(func.count(PluginVersion.pk))
                .where(PluginVersion.plugin_id == plugin_id)
                .where(PluginVersion.version >= version)) > 0:
                raise ValidationError(
                    f'Version {metadata["version"]} or higher already exists.')

            data = {
                'id': plugin_id,
                'title': metadata['name'],
                'description': metadata['description'],
                'created_by': g.user,
                'homepage': metadata.get('homepage'),
                'country': metadata.get('country'),
                'icon': metadata.get('icon_ext'),
            }
            if data['country']:
                validate_country(data['country'])

            try:
                plugin = db.session.get_one(Plugin, plugin_id)
                if plugin.created_by != g.user:
                    raise ValidationError('No permission to update')
                plugin.title = data['title']
                plugin.description = data['description']
                plugin.homepage = data['homepage']
                plugin.icon = data['icon']
            except NoResultFound:
                plugin = Plugin(**data)
                db.session.add(plugin)

            version = PluginVersion(
                plugin_id=plugin.id,
                plugin=plugin,
                version=version,
                created_by=g.user,
                experimental=metadata.get('experimental', True),
            )
            db.session.add(version)

            # Copy the file
            path = version.filename
            os.makedirs(os.path.dirname(path), exist_ok=True)
            form.package.data.save(path)
            # And the icon
            icon_file = plugin.icon_file
            if icon_file and 'icon_data' in metadata:
                with open(icon_file, 'wb') as f:
                    f.write(metadata['icon_data'])

            db.session.commit()

            return redirect(url_for('plugins.plugin', name=plugin_id))
        except ValidationError as e:
            flash(e)
        except OSError as e:
            flash(f'Error copying the file: {e}')
    return render_template('upload.html', form=form)


class PluginForm(FlaskForm):
    title = wtf.StringField(
        validators=[wtv.DataRequired(), wtv.Length(max=250)])
    description = wtf.TextAreaField()
    homepage = wtf.URLField(validators=[wtv.Optional(), wtv.URL()])
    country = wtf.StringField(
        validators=[wtv.Optional(), wtv.Length(max=32),
                    validate_country])
    hidden = wtf.BooleanField()


@bp.route('/edit/<name>', endpoint='edit', methods=['GET', 'POST'])
@get_user
@login_required
def edit_plugin(name: str):
    plugin: Plugin = db.get_or_404(Plugin, name)
    if plugin.created_by != g.user:
        raise ValidationError('No permission to update')
    form = PluginForm(obj=plugin)
    if form.validate_on_submit():
        plugin.title = form.title.data
        plugin.description = form.description.data
        plugin.homepage = form.homepage.data or None
        plugin.hidden = form.hidden.data
        plugin.country = form.country.data or None
        db.session.commit()
        return redirect(url_for('.plugin', name=name))
    return render_template('edit_plugin.html', plugin=plugin, form=form)


class VersionForm(FlaskForm):
    changelog = wtf.TextAreaField()
    experimental = wtf.BooleanField()


@bp.route('/edit/<name>/<version>', endpoint='version',
          methods=['GET', 'POST'])
@get_user
@login_required
def edit_version(name: str, version: str):
    plugin: Plugin = db.get_or_404(Plugin, name)
    if plugin.created_by != g.user:
        raise ValidationError('No permission to update')
    vobj = db.session.scalars(
        db.select(PluginVersion)
        .where(PluginVersion.plugin_id == name)
        .where(PluginVersion.version == PluginVersion.parse_version(version))
        .limit(1)
    ).one_or_none()
    if vobj is None:
        flash('No such version')
        return redirect(url_for('.plugin', name=name))

    form = VersionForm(obj=vobj)
    if form.validate_on_submit():
        vobj.changelog = form.changelog.data
        db.session.commit()
        return redirect(url_for('.plugin', name=name))
    return render_template(
        'edit_version.html', plugin=plugin, version=vobj, form=form)


@bp.route('/delete/<name>', endpoint='delete',
          methods=['GET', 'POST'])
@bp.route('/delete/<name>/<version>', endpoint='delete',
          methods=['GET', 'POST'])
@get_user
@login_required
def delete_something(name: str, version: str | None = None):
    plugin: Plugin = db.get_or_404(Plugin, name)
    if plugin.created_by != g.user:
        raise ValidationError('No permission to update')
    vobj: PluginVersion | None = None
    if version is not None:
        vobj = db.session.scalars(
            db.select(PluginVersion)
            .where(PluginVersion.plugin_id == name)
            .where(PluginVersion.version ==
                   PluginVersion.parse__version(version))
            .limit(1)
        ).one_or_none()
        if vobj is None:
            flash('No such version')
            return redirect(url_for('.plugin', name=name))

    if request.method == 'POST':
        if request.form.get('really_delete') != '1':
            return redirect(url_for('.plugin', name=name))
        db.session.delete(vobj or plugin)
        db.session.commit()
        if vobj:
            return redirect(url_for('.plugin', name=name))
        return redirect(url_for('.list'))
    return render_template('delete.html', plugin=plugin, version=vobj)


@bp.route('/i/<name>')
def install(name: str):
    url = request.args.get('url')
    if not url:
        return redirect(url_for('.plugin', name=name))
    if not re.match(r'^https?://.+/[a-zA-Z0-9_-]+\.edp$', url):
        flash(f'URL {url} does not seem to point to an EDP file.')
        return redirect(url_for('.list'))

    qr = qrcode.make(
        url, image_factory=qrcode.image.svg.SvgPathImage,
        border=1, box_size=20
    )
    plugin = db.session.scalars(
        db.select(Plugin).where(Plugin.id == name).limit(1)
    ).one_or_none()
    return render_template(
        'install.html', name=name, url=url, plugin=plugin,
        qrcode=qr.to_string().decode())


@bp.route('/<name>.edp')
@bp.route('/<name>.v<version>.edp')
def download(name: str, version: str | None = None):
    plugin = db.get_or_404(Plugin, name)
    vobj: PluginVersion | None = plugin.last_version
    if version is not None:
        vobj = db.session.scalars(
            db.select(PluginVersion)
            .where(PluginVersion.plugin_id == name)
            .where(PluginVersion.version ==
                   PluginVersion.parse_version(version))
            .limit(1)
        ).one_or_none()
    if vobj is None:
        return abort(404, f'Version {version} not found.')
    vobj.downloads += 1
    db.session.commit()
    path = vobj.filename
    return send_file(
        path, mimetype='application/x.edp+zip',
        download_name=f'{name}.v{vobj.version_str}.edp',
        as_attachment=True,
    )


@bp.route('/icon/<name>')
@bp.route('/icon/<name>.<ext>')
def icon(name: str, ext: str | None = None):
    plugin = db.get_or_404(Plugin, name)
    icon_file = plugin.icon_file
    if not icon_file:
        return abort(404, 'The plugin has no icon')
    if ext and plugin.icon != ext.lower():
        return abort(415, f'Incorrect extension, expected {plugin.icon}')
    mime = {
        'svg': 'image/svg+xml',
        'png': 'image/png',
        'gif': 'image/gif',
        'webp': 'image/webp',
    }
    return send_file(
        icon_file, mimetype=mime.get(icon_file.rsplit('.', 1)[-1]))


@bp.route('/<name>')
@get_user
def plugin(name: str):
    plugin = db.get_or_404(Plugin, name)
    plugin_url = url_for('.install', name=name, _external=True)
    qr = qrcode.make(
        plugin_url, image_factory=qrcode.image.svg.SvgPathImage,
        border=1, box_size=20
    )
    return render_template(
        'plugin.html', plugin=plugin, qrcode=qr.to_string().decode())
