import logging

import requests

logging.getLogger("requests").setLevel(logging.WARNING)
log = logging.getLogger("googlechat")


class GoogleChat:
    NAME = "GoogleChat"

    def __init__(self, webhook_url, thread_key=None):
        self.webhook_url = webhook_url
        self.thread_key = thread_key
        log.info("Initialized Google Chat notification agent")

    def send(self, **kwargs):
        if not self.webhook_url:
            log.error("You must specify a webhook_url when initializing this class")
            return False

        # send notification
        try:
            url = self.webhook_url
            if self.thread_key:
                url += f"&threadKey={self.thread_key}"

            payload = {
                'text': kwargs['message']
            }

            resp = requests.post(url, json=payload, timeout=30)
            return resp.status_code == 200

        except Exception:
            log.exception(f"Error sending notification to {self.webhook_url}")
        return False
