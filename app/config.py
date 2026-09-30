APP_NAME = "BigD"
APP_SUBTITLE = "Document governance"
APP_TAGLINE = "dream Big"
APP_TITLE = f"{APP_NAME}, {APP_TAGLINE}"


def public_config() -> dict:
    return {
        "app_name": APP_NAME,
        "app_subtitle": APP_SUBTITLE,
        "app_tagline": APP_TAGLINE,
        "app_title": APP_TITLE,
        "brand_mark": "BD",
        "logo_path": "/logo.svg",
    }
