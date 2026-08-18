"""Display-language command handler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..router import ParsedCliCommand


@dataclass(frozen=True, slots=True)
class LanguageCommandPorts:
    current_locale: Callable[[], str]
    available_locales: Callable[[], list[str]]
    translate: Callable[..., str]
    set_locale: Callable[[str], None]
    rebuild_command_lookups: Callable[[], None]
    persist_locale: Callable[[str], bool]
    emit: Callable[[str], None]


_LOCALE_PARAMETERS = {
    "-cn": "zh_CN",
    "-en": "en_US",
}


def handle_language_command(
    request: ParsedCliCommand,
    *,
    ports: LanguageCommandPorts,
) -> None:
    """Show or persist the CLI display language without accessing the host."""
    parameter = request.arguments.strip()
    if not parameter:
        _render_language_status(ports)
        return

    locale = _LOCALE_PARAMETERS.get(parameter.lower())
    if locale is None:
        ports.emit(
            f"  {ports.translate('language_command.invalid_param', param=parameter)}"
        )
        ports.emit(f"  {ports.translate('language_command.usage')}")
        return

    ports.set_locale(locale)
    ports.rebuild_command_lookups()
    if ports.persist_locale(locale):
        ports.emit(f"  {ports.translate('language_command.set_to_saved', locale=locale)}")
    else:
        ports.emit(f"  {ports.translate('language_command.set_to', locale=locale)}")
    switched_key = (
        'language_command.switched_cn'
        if locale == 'zh_CN'
        else 'language_command.switched_en'
    )
    ports.emit(f"  {ports.translate(switched_key)}")


def _render_language_status(ports: LanguageCommandPorts) -> None:
    current = ports.current_locale()
    available = ports.available_locales() or ["zh_CN", "en_US"]

    current_label = ports.translate(
        'language_command.current',
        locale=current,
        name=_language_name(current, ports),
    )
    ports.emit(f"\n  {current_label}")
    ports.emit(f"  {ports.translate('language_command.available')}")
    for locale in available:
        marker = " ●" if locale == current else "  "
        ports.emit(f"   {marker} {locale} - {_language_name(locale, ports)}")

    ports.emit(f"\n  {ports.translate('language_command.usage')}")
    ports.emit(f"  {ports.translate('language_command.examples')}")
    ports.emit(f"    {ports.translate('language_command.example_show')}")
    ports.emit(f"    {ports.translate('language_command.example_cn')}")
    ports.emit(f"    {ports.translate('language_command.example_en')}")
    ports.emit(f"\n  {ports.translate('language_command.tip_env')}\n")


def _language_name(locale: str, ports: LanguageCommandPorts) -> str:
    names = {
        "zh_CN": ports.translate(
            'language_command.lang_zh_CN', default='简体中文'
        ),
        "en_US": ports.translate(
            'language_command.lang_en_US', default='English'
        ),
    }
    return names.get(locale, locale)
