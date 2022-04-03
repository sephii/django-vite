import dataclasses
import enum
import json
from pathlib import Path
from typing import Dict, List
from urllib.parse import urljoin

from django import template
from django.conf import settings
from django.utils.safestring import mark_safe

register = template.Library()


# If using in development or production mode.
DJANGO_VITE_DEV_MODE = getattr(settings, "DJANGO_VITE_DEV_MODE", False)

# Default Vite server protocol (http or https)
DJANGO_VITE_DEV_SERVER_PROTOCOL = getattr(
    settings, "DJANGO_VITE_DEV_SERVER_PROTOCOL", "http"
)

# Default vite server hostname.
DJANGO_VITE_DEV_SERVER_HOST = getattr(
    settings, "DJANGO_VITE_DEV_SERVER_HOST", "localhost"
)

# Default Vite server port.
DJANGO_VITE_DEV_SERVER_PORT = getattr(
    settings, "DJANGO_VITE_DEV_SERVER_PORT", 3000
)

# Default Vite server path to HMR script.
DJANGO_VITE_WS_CLIENT_URL = getattr(
    settings, "DJANGO_VITE_WS_CLIENT_URL", "@vite/client"
)

# Location of Vite compiled assets (only used in Vite production mode).
# Must be included in your "STATICFILES_DIRS".
# In Django production mode this folder need to be collected as static
# files using "python manage.py collectstatic".
DJANGO_VITE_ASSETS_PATH = Path(getattr(settings, "DJANGO_VITE_ASSETS_PATH"))

# Prefix for STATIC_URL
DJANGO_VITE_STATIC_URL_PREFIX = getattr(
    settings, "DJANGO_VITE_STATIC_URL_PREFIX", ""
)

DJANGO_VITE_STATIC_ROOT = (
    DJANGO_VITE_ASSETS_PATH
    if DJANGO_VITE_DEV_MODE
    else Path(settings.STATIC_ROOT) / DJANGO_VITE_STATIC_URL_PREFIX
)

# Path to your manifest file generated by Vite.
# Should by in "DJANGO_VITE_ASSETS_PATH".
DJANGO_VITE_MANIFEST_PATH = getattr(
    settings,
    "DJANGO_VITE_MANIFEST_PATH",
    DJANGO_VITE_STATIC_ROOT / "manifest.json",
)

# Motif in the 'manifest.json' to find the polyfills generated by Vite.
DJANGO_VITE_LEGACY_POLYFILLS_MOTIF = getattr(
    settings, "DJANGO_VITE_LEGACY_POLYFILLS_MOTIF", "legacy-polyfills"
)

DJANGO_VITE_STATIC_URL = urljoin(
    settings.STATIC_URL, DJANGO_VITE_STATIC_URL_PREFIX
)

# Make sure 'DJANGO_VITE_STATIC_URL' finish with a '/'
if DJANGO_VITE_STATIC_URL[-1] != "/":
    DJANGO_VITE_STATIC_URL += "/"


class AssetType(enum.Enum):
    SCRIPT = "script"
    STYLE = "style"


@dataclasses.dataclass
class Asset:
    path: str
    asset_type: AssetType
    attrs: Dict[str, str] = dataclasses.field(default_factory=dict)

    def __html__(self):
        if self.asset_type == AssetType.SCRIPT:
            return DjangoViteAssetLoader._generate_script_tag(
                urljoin(DJANGO_VITE_STATIC_URL, self.path),
                attrs=self.attrs,
            )
        elif self.asset_type == AssetType.STYLE:
            return DjangoViteAssetLoader._generate_stylesheet_tag(
                urljoin(DJANGO_VITE_STATIC_URL, self.path)
            )

        raise ValueError(f"Unsupported asset type {self.asset_type}")

    def __str__(self):
        return mark_safe(self.__html__())


@dataclasses.dataclass
class AssetsCollection:
    assets: List[Asset] = dataclasses.field(default_factory=list)

    # Using __html__ is necessary so the output is not escaped by the template
    # engine when using `{% vite_asset %}` (which will call `conditional_escape`
    # directly on the AssetsCollection object)
    def __html__(self):
        return "\n".join(asset.__html__() for asset in self.assets)

    def __str__(self):
        return mark_safe(self.__html__())

    def _assets_by_type(self, asset_type: AssetType) -> "AssetsCollection":
        return AssetsCollection(
            assets=[
                asset
                for asset in self.assets
                if asset.asset_type == asset_type
            ]
        )

    @property
    def styles(self):
        return self._assets_by_type(AssetType.STYLE)

    @property
    def scripts(self):
        return self._assets_by_type(AssetType.SCRIPT)


class DjangoViteAssetLoader:
    """
    Class handling Vite asset loading.
    """

    _instance = None

    def __init__(self) -> None:
        raise RuntimeError("Use the instance() method instead.")

    def generate_vite_asset(
        self,
        path: str,
        **kwargs: Dict[str, str],
    ) -> AssetsCollection:
        """
        Generates a <script> tag for this JS/TS asset and a <link> tag for
        all of its CSS dependencies by reading the manifest
        file (for production only).
        In development Vite loads all by itself.

        Arguments:
            path {str} -- Path to a Vite JS/TS asset to include.

        Returns:
            str -- All tags to import this file in your HTML page.

        Keyword Arguments:
            **kwargs {Dict[str, str]} -- Adds new attributes to generated
                script tags.

        Raises:
            RuntimeError: If cannot find the file path in the
                manifest (only in production).

        Returns:
            str -- The <script> tag and all <link> tags to import
                this asset in your page.
        """

        if DJANGO_VITE_DEV_MODE:
            return AssetsCollection(
                assets=[
                    Asset(
                        path=DjangoViteAssetLoader._generate_vite_server_url(
                            path
                        ),
                        attrs={"type": "module"},
                        asset_type=AssetType.SCRIPT,
                    )
                ]
            )

        if not self._manifest or path not in self._manifest:
            raise RuntimeError(
                f"Cannot find {path} in Vite manifest "
                f"at {DJANGO_VITE_MANIFEST_PATH}"
            )

        manifest_entry = self._manifest[path]
        scripts_attrs = {"type": "module", "crossorigin": "", **kwargs}

        # Add dependent CSS
        css_assets = self._generate_css_files_of_asset(path, [])

        # Add the script by itself
        js_asset = Asset(
            path=manifest_entry["file"],
            asset_type=AssetType.SCRIPT,
            attrs=scripts_attrs,
        )

        return AssetsCollection(assets=css_assets + [js_asset])

    def _generate_css_files_of_asset(
        self, path: str, already_processed: List[str]
    ) -> List[Asset]:
        """
        Generates all CSS tags for dependencies of an asset.

        Arguments:
            path {str} -- Path to an asset in the 'manifest.json'.
            already_processed {list} -- List of already processed CSS file.

        Returns:
            list -- List of CSS tags.
        """

        tags = []
        manifest_entry = self._manifest[path]

        if "imports" in manifest_entry:
            for import_path in manifest_entry["imports"]:
                tags.extend(
                    self._generate_css_files_of_asset(
                        import_path, already_processed
                    )
                )

        if "css" in manifest_entry:
            for css_path in manifest_entry["css"]:
                if css_path not in already_processed:
                    tags.append(
                        Asset(asset_type=AssetType.STYLE, path=css_path)
                    )

                already_processed.append(css_path)

        return tags

    def generate_vite_asset_url(self, path: str) -> str:
        """
        Generates only the URL of an asset managed by ViteJS.
        Warning, this function does not generate URLs for dependant assets.

        Arguments:
            path {str} -- Path to a Vite asset.

        Raises:
            RuntimeError: If cannot find the asset path in the
                manifest (only in production).

        Returns:
            str -- The URL of this asset.
        """

        if DJANGO_VITE_DEV_MODE:
            return DjangoViteAssetLoader._generate_vite_server_url(path)

        if not self._manifest or path not in self._manifest:
            raise RuntimeError(
                f"Cannot find {path} in Vite manifest "
                f"at {DJANGO_VITE_MANIFEST_PATH}"
            )

        return urljoin(DJANGO_VITE_STATIC_URL, self._manifest[path]["file"])

    def generate_vite_legacy_polyfills(
        self,
        **kwargs: Dict[str, str],
    ) -> str:
        """
        Generates a <script> tag to the polyfills
        generated by '@vitejs/plugin-legacy' if used.
        This tag must be included at end of the <body> before
        including other legacy scripts.

        Keyword Arguments:
            **kwargs {Dict[str, str]} -- Adds new attributes to generated
                script tags.

        Raises:
            RuntimeError: If polyfills path not found inside
                the 'manifest.json' (only in production).

        Returns:
            str -- The script tag to the polyfills.
        """

        if DJANGO_VITE_DEV_MODE:
            return ""

        scripts_attrs = {"nomodule": "", "crossorigin": "", **kwargs}

        for path, content in self._manifest.items():
            if DJANGO_VITE_LEGACY_POLYFILLS_MOTIF in path:
                return DjangoViteAssetLoader._generate_script_tag(
                    urljoin(DJANGO_VITE_STATIC_URL, content["file"]),
                    attrs=scripts_attrs,
                )

        raise RuntimeError(
            f"Vite legacy polyfills not found in manifest "
            f"at {DJANGO_VITE_MANIFEST_PATH}"
        )

    def generate_vite_legacy_asset(
        self,
        path: str,
        **kwargs: Dict[str, str],
    ) -> str:
        """
        Generates a <script> tag for legacy assets JS/TS
        generated by '@vitejs/plugin-legacy'
        (in production only, in development do nothing).

        Arguments:
            path {str} -- Path to a Vite asset to include
                (must contains '-legacy' in its name).

        Keyword Arguments:
            **kwargs {Dict[str, str]} -- Adds new attributes to generated
                script tags.

        Raises:
            RuntimeError: If cannot find the asset path in the
                manifest (only in production).

        Returns:
            str -- The script tag of this legacy asset .
        """

        if DJANGO_VITE_DEV_MODE:
            return ""

        if not self._manifest or path not in self._manifest:
            raise RuntimeError(
                f"Cannot find {path} in Vite manifest "
                f"at {DJANGO_VITE_MANIFEST_PATH}"
            )

        manifest_entry = self._manifest[path]
        scripts_attrs = {"nomodule": "", "crossorigin": "", **kwargs}

        return DjangoViteAssetLoader._generate_script_tag(
            urljoin(DJANGO_VITE_STATIC_URL, manifest_entry["file"]),
            attrs=scripts_attrs,
        )

    def _parse_manifest(self) -> None:
        """
        Read and parse the Vite manifest file.

        Raises:
            RuntimeError: if cannot load the file or JSON in file is malformed.
        """

        try:
            manifest_file = open(DJANGO_VITE_MANIFEST_PATH, "r")
            manifest_content = manifest_file.read()
            manifest_file.close()
            self._manifest = json.loads(manifest_content)
        except Exception as error:
            raise RuntimeError(
                f"Cannot read Vite manifest file at "
                f"{DJANGO_VITE_MANIFEST_PATH} : {str(error)}"
            )

    @classmethod
    def instance(cls):
        """
        Singleton.
        Uses singleton to keep parsed manifest in memory after
        the first time it's loaded.

        Returns:
            DjangoViteAssetLoader -- only instance of the class.
        """

        if cls._instance is None:
            cls._instance = cls.__new__(cls)
            cls._instance._manifest = None

            # Manifest is only used in production.
            if not DJANGO_VITE_DEV_MODE:
                cls._instance._parse_manifest()

        return cls._instance

    @classmethod
    def generate_vite_ws_client(cls) -> str:
        """
        Generates the script tag for the Vite WS client for HMR.
        Only used in development, in production this method returns
        an empty string.

        Returns:
            str -- The script tag or an empty string.
        """

        if not DJANGO_VITE_DEV_MODE:
            return ""

        return cls._generate_script_tag(
            cls._generate_vite_server_url(DJANGO_VITE_WS_CLIENT_URL),
            {"type": "module"},
        )

    @staticmethod
    def _generate_script_tag(src: str, attrs: Dict[str, str]) -> str:
        """
        Generates an HTML script tag.

        Arguments:
            src {str} -- Source of the script.

        Keyword Arguments:
            attrs {Dict[str, str]} -- List of custom attributes
                for the tag.

        Returns:
            str -- The script tag.
        """

        attrs_str = " ".join(
            [f'{key}="{value}"' for key, value in attrs.items()]
        )

        return f'<script {attrs_str} src="{src}"></script>'

    @staticmethod
    def _generate_stylesheet_tag(href: str) -> str:
        """
        Generates and HTML <link> stylesheet tag for CSS.

        Arguments:
            href {str} -- CSS file URL.

        Returns:
            str -- CSS link tag.
        """

        return f'<link rel="stylesheet" href="{href}" />'

    @staticmethod
    def _generate_vite_server_url(path: str) -> str:
        """
        Generates an URL to and asset served by the Vite development server.

        Keyword Arguments:
            path {str} -- Path to the asset.

        Returns:
            str -- Full URL to the asset.
        """

        return urljoin(
            f"{DJANGO_VITE_DEV_SERVER_PROTOCOL}://"
            f"{DJANGO_VITE_DEV_SERVER_HOST}:{DJANGO_VITE_DEV_SERVER_PORT}",
            urljoin(DJANGO_VITE_STATIC_URL, path),
        )


@register.simple_tag
@mark_safe
def vite_hmr_client() -> str:
    """
    Generates the script tag for the Vite WS client for HMR.
    Only used in development, in production this method returns
    an empty string.

    Returns:
        str -- The script tag or an empty string.
    """

    return DjangoViteAssetLoader.generate_vite_ws_client()


@register.simple_tag
def vite_asset(
    path: str,
    **kwargs: Dict[str, str],
) -> AssetsCollection:
    """
    Generates a <script> tag for this JS/TS asset and a <link> tag for
    all of its CSS dependencies by reading the manifest
    file (for production only).
    In development Vite loads all by itself.

    Arguments:
        path {str} -- Path to a Vite JS/TS asset to include.

    Returns:
        str -- All tags to import this file in your HTML page.

    Keyword Arguments:
        **kwargs {Dict[str, str]} -- Adds new attributes to generated
            script tags.

    Raises:
        RuntimeError: If cannot find the file path in the
            manifest (only in production).

    Returns:
        str -- The <script> tag and all <link> tags to import this
            asset in your page.
    """

    assert path is not None

    return DjangoViteAssetLoader.instance().generate_vite_asset(path, **kwargs)


@register.simple_tag
def vite_asset_url(path: str) -> str:
    """
    Generates only the URL of an asset managed by ViteJS.
    Warning, this function does not generate URLs for dependant assets.

    Arguments:
        path {str} -- Path to a Vite asset.

    Raises:
        RuntimeError: If cannot find the asset path in the
            manifest (only in production).

    Returns:
        str -- The URL of this asset.
    """

    assert path is not None

    return DjangoViteAssetLoader.instance().generate_vite_asset_url(path)


@register.simple_tag
@mark_safe
def vite_legacy_polyfills(**kwargs: Dict[str, str]) -> str:
    """
    Generates a <script> tag to the polyfills generated
    by '@vitejs/plugin-legacy' if used.
    This tag must be included at end of the <body> before including
    other legacy scripts.

    Keyword Arguments:
        **kwargs {Dict[str, str]} -- Adds new attributes to generated
            script tags.

    Raises:
        RuntimeError: If polyfills path not found inside
            the 'manifest.json' (only in production).

    Returns:
        str -- The script tag to the polyfills.
    """

    return DjangoViteAssetLoader.instance().generate_vite_legacy_polyfills(
        **kwargs
    )


@register.simple_tag
@mark_safe
def vite_legacy_asset(
    path: str,
    **kwargs: Dict[str, str],
) -> str:
    """
    Generates a <script> tag for legacy assets JS/TS
    generated by '@vitejs/plugin-legacy'
    (in production only, in development do nothing).

    Arguments:
        path {str} -- Path to a Vite asset to include
            (must contains '-legacy' in its name).

    Keyword Arguments:
        **kwargs {Dict[str, str]} -- Adds new attributes to generated
            script tags.

    Raises:
        RuntimeError: If cannot find the asset path in
            the manifest (only in production).

    Returns:
        str -- The script tag of this legacy asset .
    """

    assert path is not None

    return DjangoViteAssetLoader.instance().generate_vite_legacy_asset(
        path, **kwargs
    )
