from __future__ import annotations

import sys

from VoidCube_cli.entrypoint_startup import _require_tty


def cmd_model(args):
    """Switch model/provider within the configured provider list."""
    _require_tty("model")
    select_provider_and_model(args=args)


def select_provider_and_model(args=None):
    """Switch active provider/model only within the saved provider config."""
    from VoidCube_app.config import (
        get_active_provider_key,
        get_configured_providers,
        load_config,
        save_config,
        set_active_provider,
        set_provider_model,
    )
    from VoidCube_app.models import curated_models_for_provider

    config = load_config()
    providers = get_configured_providers(config)
    active_provider = get_active_provider_key(config)

    if not providers:
        print()
        print("No configured providers found.")
        print("Run `VoidCube api` first to add a provider, then use `VoidCube model` to switch.")
        print()
        return

    ordered_keys = sorted(
        providers.keys(),
        key=lambda key: (key != active_provider, str(providers.get(key, {}).get("label") or key).lower()),
    )
    provider_choices = []
    default_idx = 0
    for idx, provider_key in enumerate(ordered_keys):
        provider_cfg = providers.get(provider_key, {})
        label = str(provider_cfg.get("label") or provider_key)
        current_model = str(provider_cfg.get("selected_model") or "").strip()
        suffix = f" [{current_model}]" if current_model else ""
        if provider_key == active_provider:
            provider_choices.append(f"{label} ({provider_key}){suffix}  ← active")
            default_idx = idx
        else:
            provider_choices.append(f"{label} ({provider_key}){suffix}")

    print()
    print(f"  Active provider:  {active_provider or 'not configured'}")
    current_active_model = ""
    if active_provider and active_provider in providers:
        current_active_model = str(providers[active_provider].get("selected_model") or "").strip()
    print(f"  Active model:     {current_active_model or 'not set'}")
    print()

    provider_idx = _prompt_provider_choice(provider_choices, default=default_idx)
    if provider_idx is None:
        print("No change.")
        return

    selected_provider = ordered_keys[provider_idx]
    provider_cfg = providers.get(selected_provider, {})
    saved_model = str(provider_cfg.get("selected_model") or "").strip()

    curated_models = []
    try:
        curated_models = [mid for mid, _ in curated_models_for_provider(selected_provider)]
    except Exception:
        curated_models = []

    model_choices: list[str] = []
    if saved_model:
        model_choices.append(saved_model)
    for model_id in curated_models:
        if model_id and model_id not in model_choices:
            model_choices.append(model_id)
    model_choices = model_choices[:20]

    print(f"Selected provider: {selected_provider}")
    if provider_cfg.get("base_url"):
        print(f"Endpoint: {provider_cfg.get('base_url')}")
    print()

    selected_model = saved_model
    if model_choices:
        numbered_choices = list(model_choices)
        numbered_choices.append("Enter custom model name")
        numbered_choices.append("Cancel")
        model_idx = _prompt_provider_choice(
            [
                f"{choice}  ← current" if choice == saved_model and saved_model else choice
                for choice in numbered_choices
            ],
            default=0,
        )
        if model_idx is None or model_idx == len(numbered_choices) - 1:
            print("No change.")
            return
        if model_idx == len(numbered_choices) - 2:
            try:
                selected_model = input("Model name: ").strip()
            except EOFError:
                print()
                print("No change.")
                return
        else:
            selected_model = numbered_choices[model_idx]
    else:
        prompt = "Model name"
        if saved_model:
            prompt += f" [{saved_model}]"
        prompt += ": "
        try:
            entered = input(prompt).strip()
        except EOFError:
            print()
            print("No change.")
            return
        selected_model = entered or saved_model

    if not selected_model:
        print("No model selected. Run `VoidCube api` to configure a provider or choose a model here.")
        return

    config = set_provider_model(config, selected_provider, selected_model, make_active=True)
    config = set_active_provider(config, selected_provider)
    save_config(config)

    print()
    print(f"Saved active provider: {selected_provider}")
    print(f"Saved active model:    {selected_model}")
    print()


def _prompt_provider_choice(choices, *, default=0):
    """Show provider selection menu with curses arrow-key navigation.

    Falls back to a numbered list when curses is unavailable (e.g. piped
    stdin, non-TTY environments).  Returns the selected index, or None
    if the user cancels.
    """
    try:
        from VoidCube_cli.curses_ui import curses_single_select
        idx = curses_single_select("Select provider:", choices, default)
        if idx is not None:
            print()
            return idx
    except Exception:
        pass

    # Fallback: numbered list
    print("Select provider:")
    for i, c in enumerate(choices, 1):
        marker = "→" if i - 1 == default else " "
        print(f"  {marker} {i}. {c}")
    print()
    while True:
        try:
            val = input(f"Choice [1-{len(choices)}] ({default + 1}): ").strip()
            if not val:
                return default
            idx = int(val) - 1
            if 0 <= idx < len(choices):
                return idx
            print(f"Please enter 1-{len(choices)}")
        except ValueError:
            print("Please enter a number")
        except EOFError:
            print()
            return None


def cmd_login(args):
    """Authenticate Voidcube CLI with a provider."""
    from VoidCube_cli.auth import login_command
    login_command(args)


def cmd_logout(args):
    """Clear provider authentication."""
    from VoidCube_cli.auth import logout_command
    logout_command(args)




def cmd_status(args):
    """Show status of all components."""
    from VoidCube_cli.status import show_status
    show_status(args)
