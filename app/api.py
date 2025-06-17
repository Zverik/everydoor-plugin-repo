from flask import Blueprint, url_for, request
from typing import Any
from sqlalchemy import or_
from .database import db, Plugin


bp = Blueprint('api', __name__)


def plugin_to_dict(plugin: Plugin, experimental=False,
                   version: int | None = None):
    result: dict[str, Any] = {
        'id': plugin.id,
        'name': plugin.title,
        'description': plugin.description,
        'author': plugin.created_by.name,
        'url': url_for('plugins.plugin', name=plugin.id,
                       _external=True),
        'download': url_for('plugins.download', name=plugin.id,
                            _external=True),
    }
    if plugin.homepage:
        result['homepage'] = plugin.homepage
    if plugin.country:
        result['country'] = plugin.country
    if plugin.hidden:
        result['hidden'] = True

    if plugin.icon:
        result['icon'] = url_for(
            'plugins.icon', name=plugin.id, ext=plugin.icon, _external=True)

    result['downloads'] = plugin.downloads

    vobj = plugin.last_eversion if experimental else plugin.last_version
    if vobj:
        result['version'] = vobj.version_str
        result['updated'] = vobj.created_on.isoformat(' ')
        result['experimental'] = vobj.experimental
        result['download'] = url_for(
            'plugins.download', name=plugin.id, version=vobj.version,
            _external=True)
    else:
        return None
    return result


@bp.route('/list', endpoint='list')
def list_plugins():
    countries = [c for c in request.args.get('countries', '').split(',') if c]
    q = db.select(Plugin).where(~Plugin.hidden)
    if countries:
        q = q.where(or_(Plugin.country.is_(None),
                        Plugin.country.in_(countries)))
    else:
        q = q.where(Plugin.country.is_(None))

    plugins = db.session.scalars(q.order_by(Plugin.title))
    exp = request.args.get('exp') == '1'
    result = (plugin_to_dict(p, exp) for p in plugins)
    return [r for r in result if r]


@bp.route('/plugin/<name>')
def plugin(name: str):
    plugin: Plugin = db.get_or_404(Plugin, name)
    return plugin_to_dict(plugin)
