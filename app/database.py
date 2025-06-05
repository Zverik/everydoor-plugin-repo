import string
import random
import os
from flask import current_app
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import String, ForeignKey, func, sql
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, aliased,
)
from sqlalchemy.ext.hybrid import hybrid_property


class Base(DeclarativeBase):
    pass


db = SQLAlchemy(model_class=Base)


class User(db.Model):
    osm_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(server_default=sql.false())
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    def update_token(self):
        chars = string.ascii_letters + string.digits + '_'
        self.token = ''.join(random.choices(chars, k=32))

    def __repr__(self) -> str:
        return f'User(osm_id={self.osm_id!r}, name={self.name!r})'


class Plugin(db.Model):
    id: Mapped[str] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(250), unique=True)
    description: Mapped[str]
    created_by_id: Mapped[int] = mapped_column(ForeignKey('user.osm_id'))
    created_by: Mapped[User] = relationship()
    homepage: Mapped[str | None]
    country: Mapped[str | None] = mapped_column(String(32))
    hidden: Mapped[bool] = mapped_column(server_default=sql.false())
    icon: Mapped[str | None]

    versions: Mapped[list["PluginVersion"]] = relationship(
        back_populates='plugin', order_by='desc(PluginVersion.created_on)',
        cascade='all, delete',
    )

    @property
    def icon_file(self) -> str | None:
        if not self.icon:
            return None
        return os.path.join(
            current_app.instance_path, 'plugins', self.id,
            f'icon.{self.icon}')

    @hybrid_property
    def downloads(self):
        return sum(v.downloads for v in self.versions)

    @downloads.expression
    def _downloads_expr(cls):
        return (
            db.select(func.sum(PluginVersion.downloads))
            .where(PluginVersion.plugin_id == cls.id)
            .label('downloads')
        )


class PluginVersion(db.Model):
    pk: Mapped[int] = mapped_column(primary_key=True)
    plugin_id: Mapped[str] = mapped_column(ForeignKey('plugin.id'))
    plugin: Mapped[Plugin] = relationship(back_populates='versions')
    created_on: Mapped[datetime] = mapped_column(
        server_default=func.CURRENT_TIMESTAMP())
    version: Mapped[int]
    downloads: Mapped[int] = mapped_column(server_default='0')
    created_by_id: Mapped[int] = mapped_column(ForeignKey('user.osm_id'))
    created_by: Mapped[User] = relationship()
    changelog: Mapped[str | None]
    experimental: Mapped[bool] = mapped_column(server_default=sql.true())

    @property
    def filename(self) -> str:
        return os.path.join(
            current_app.instance_path, 'plugins', self.plugin_id,
            f'{self.version}.edp')

    @property
    def version_str(self) -> str:
        if self.version < 1000:
            return str(self.version)
        major = self.version // 1000 - 1
        minor = self.version % 1000
        return f'{major}.{minor}'

    @staticmethod
    def parse_version(value: str | int) -> int:
        if isinstance(value, int):
            if value > 1000:
                raise ValueError('Version over 1000 is ambiguous')
            return value

        if isinstance(value, float):
            value = str(value)
            if '.' not in value:
                value = f'{value}.0'

        if '.' in value:
            parts = [int(p.strip()) for p in value.split('.')]
            if parts[1] > 1000:
                raise ValueError('Minor version over 1000')
            return (parts[0] + 1) * 1000 + parts[1]

        result = int(value)
        if result > 1000:
            raise ValueError('Version over 1000 is ambiguous')
        return result


latest_version_subquery = (
    db.select(PluginVersion).distinct(PluginVersion.plugin_id)
    .where(~PluginVersion.experimental)
    .order_by(PluginVersion.plugin_id, PluginVersion.created_on.desc())
    .limit(1).alias()
)
latest_version_alias = aliased(PluginVersion, latest_version_subquery)
Plugin.last_version = relationship(
    latest_version_alias,
    uselist=False,
    lazy='selectin',
)

latest_eversion_subquery = (
    db.select(PluginVersion).distinct(PluginVersion.plugin_id)
    .order_by(PluginVersion.plugin_id, PluginVersion.created_on.desc())
    .limit(1).alias()
)
latest_eversion_alias = aliased(PluginVersion, latest_eversion_subquery)
Plugin.last_eversion = relationship(
    latest_eversion_alias,
    uselist=False,
    lazy='selectin',
)
