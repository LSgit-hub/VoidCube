from voidcube.systems.supervisor.config_models import SupervisorMailConfig, SupervisorServiceRuntimeConfig
from voidcube.systems.supervisor.mail_runtime import MailSettings, build_mail_overview


def test_supervisor_service_runtime_includes_mail_defaults():
    runtime = SupervisorServiceRuntimeConfig()
    assert isinstance(runtime.mail, SupervisorMailConfig)
    assert runtime.mail.inbox_folder == "INBOX"
    assert runtime.mail.sent_folder == "Sent"
    assert runtime.mail.smtp_use_starttls is True
    assert runtime.mail.imap_use_ssl is True


def test_mail_settings_overview_masks_password_and_reports_configured_state():
    settings = MailSettings.from_mapping(
        {
            "enabled": True,
            "display_name": "星子邮件",
            "address": "xingzi@example.com",
            "username": "xingzi",
            "password": "secret",
            "imap_host": "imap.example.com",
            "smtp_host": "smtp.example.com",
        }
    )

    payload = build_mail_overview(settings)

    assert payload["configured"] is True
    assert payload["password_set"] is True
    assert payload["address"] == "xingzi@example.com"
    assert "password" not in payload
