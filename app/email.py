import smtplib
from email.message import EmailMessage

class Mailer:
    def __init__(self, settings):
        self.settings = settings

    @property
    def configured(self):
        return bool(self.settings.smtp_host and self.settings.from_email)

    def send_test(self, recipient, name):
        if not self.configured:
            raise RuntimeError('Email delivery is not configured.')
        message = EmailMessage()
        message['Subject'] = 'Career Quest is connected'
        message['From'] = self.settings.from_email
        message['To'] = recipient
        message.set_content(
            f'Hi {name},\n\nYour Career Quest email is connected. '
            'You can receive weekly plan reminders and relevant opportunity suggestions.\n\nCareer Quest'
        )
        with smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=10) as server:
            if self.settings.smtp_tls:
                server.starttls()
            if self.settings.smtp_username:
                server.login(self.settings.smtp_username, self.settings.smtp_password)
            server.send_message(message)
