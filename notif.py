from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
from requests.exceptions import RequestException
from dotenv import load_dotenv
import os, json, requests
load_dotenv()

from enum import StrEnum
class Severity(StrEnum):
    Info = '[INFO]'
    Warn = '[WARNING]'
    Crit = '[CRIT]'

TOO_MANY_REQUESTS_ERR_CODE = 429
wait = 5 # seconds
@retry(
    stop=stop_after_attempt(3),
    wait=wait_fixed(wait),
    retry=retry_if_exception_type(ConnectionRefusedError),
    reraise=True,
)
def send_notification_to_slack(severity: Severity, message: str):
    webhook_url = os.getenv('SLACK_WEBHOOK_URL')
    if not webhook_url:
        # raise ValueError('Slack Webhook Environment Variable DNE')
        return 0

    headers = {
        "Content-Type": "application/json"
    }

    payload = {
        "text": severity + '\n' + message
    }

    try:
        response = requests.post(webhook_url, data=json.dumps(payload), headers=headers)
        response.raise_for_status() # Raise an exception for HTTP errors (4xx or 5xx)
        print("Slack notification sent")
        return response.status_code
    except RequestException as e:
        print(f"Failed to send Slack notification: {e}")
        if e.response is not None and e.response.status_code == TOO_MANY_REQUESTS_ERR_CODE:
            print('Rate Limited')
            global wait
            wait = e.response.headers.get('Retry-After')
            print(f"retrying after {wait} seconds...")
            raise ConnectionRefusedError(e)

if __name__ == "__main__":
    send_notification_to_slack(Severity.Info, 'This is a test message')