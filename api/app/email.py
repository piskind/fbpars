import asyncio
from loguru import logger


async def send_verification_email(to_email: str, token: str) -> bool:
    from app.config import settings

    if not settings.resend_api_key:
        logger.warning("RESEND_API_KEY not set, skipping verification email")
        return False

    verify_url = f"{settings.frontend_base_url.rstrip('/')}/verify/{token}"

    html_body = f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#fff;font-family:Arial,sans-serif;color:#111">
  <div style="max-width:480px;margin:40px auto;padding:32px;border:1px solid #eee;border-radius:8px">
    <h2 style="margin-top:0;margin-bottom:16px">Подтвердите email для FB Spy</h2>
    <p style="margin-bottom:24px;color:#333">
      Для активации аккаунта нажмите на кнопку ниже:
    </p>
    <a href="{verify_url}"
       style="display:inline-block;background:#2563eb;color:#fff;text-decoration:none;
              padding:12px 24px;border-radius:6px;font-weight:600">
      Подтвердить email
    </a>
    <p style="margin-top:24px;font-size:12px;color:#999">
      Если вы не регистрировались на FB Spy — проигнорируйте это письмо.
    </p>
  </div>
</body>
</html>"""

    try:
        import resend
        resend.api_key = settings.resend_api_key
        params: resend.Emails.SendParams = {
            "from": "noreply@mail.spyon.top",
            "to": [to_email],
            "subject": "Подтвердите email для FB Spy",
            "html": html_body,
        }
        await asyncio.to_thread(resend.Emails.send, params)
        logger.info(f"Verification email sent to {to_email}")
        return True
    except Exception as e:
        logger.warning(f"Failed to send verification email to {to_email}: {e}")
        return False
