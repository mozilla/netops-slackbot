#!/usr/bin/env python
import os
import time
import re
from slackclient import SlackClient
import yaml
import json
import requests

with open("config.yml", 'r') as ymlfile:
    cfg = yaml.load(ymlfile)

# instantiate Slack client
slack_client = SlackClient(cfg['slack_api_token'])

# starterbot's user ID in Slack: value is assigned after the bot starts up
starterbot_id = None

# oncall person object
oncall = cfg["default_oncall"]

# constants
RTM_READ_DELAY = 1 # 1 second delay between reading from RTM
EXAMPLE_COMMAND = "do"
MENTION_REGEX = "^<@(|[WU].+?)>(.*)"

def get_oncall():
    headers = {
        'Authorization': 'Token token={0}'.format(cfg["pagerduty_api_token"]),
        'Content-Type': 'application/vnd.pagerduty+json;version=2',
    }
    url = 'https://api.pagerduty.com/oncalls?time_zone=UTC&include%5B%5D=users&escalation_policy_ids%5B%5D={0}&schedule_ids%5B%5D={1}'.format(cfg["pagerduty_escalation_policy"],cfg["pagerduty_oncall_schedule"])
    try:
        r = requests.get(url, headers=headers, timeout=30)
    except requests.Timeout:
        print "PagerDuty API timed out!"

    decoded = json.loads(r.text)
    oncall = decoded["oncalls"][0]["user"]
    oncall["start"] = decoded["oncalls"][0]["start"]
    oncall["end"] = decoded["oncalls"][0]["end"]
    # TODO: parse text of "description" field ("Bio" in the UI) to get IRC
    # and Slack if present, but fall back to first part of email if not given
    oncall["irc_nick"] = oncall["email"].split("@")[0]
    oncall["slack_nick"] = oncall["email"].split("@")[0]
    return oncall

def parse_bot_commands(slack_events):
    """
        Parses a list of events coming from the Slack RTM API to find bot commands.
        If a bot command is found, this function returns a tuple of command and channel.
        If its not found, then this function returns None, None.
    """
    for event in slack_events:
        if event["type"] == "message" and not "subtype" in event:
            user_id, message = parse_direct_mention(event["text"])
            if user_id == starterbot_id:
                return message, event["channel"]
    return None, None

def parse_direct_mention(message_text):
    """
        Finds a direct mention (a mention that is at the beginning) in message text
        and returns the user ID which was mentioned. If there is no direct mention, returns None
    """
    matches = re.search(MENTION_REGEX, message_text)
    # the first group contains the username, the second group contains the remaining message
    return (matches.group(1), matches.group(2).strip()) if matches else (None, None)

def handle_command(command, channel):
    """
        Executes bot command if the command is known
    """
    # Default response is help text for the user
    default_response = "Not sure what you mean. Try *{}*.".format(EXAMPLE_COMMAND)

    # Finds and executes the given command, filling in response
    response = None
    # This is where you start to implement more commands!
    if command.startswith(EXAMPLE_COMMAND):
        response = "Sure...write some more code then I can do that!"

    if command.startswith("oncall"):
        attachments = json.dumps([{
            "color": "#36a64f",
            "pretext": "The current oncall network engineer is:",
            "title": oncall["name"],
            "fields": [{
                "title": "IRC",
                "value": oncall["irc_nick"],
                "short": "true"
            },
            {
                "title": "Slack",
                "value": oncall["slack_nick"],
                "short": "true"
            },
            {
                "title": "Email",
                "value": oncall["email"],
                "short": "true"
            }],
            "thumb_url": oncall["avatar_url"],
            "footer": "Oncall from {0} to {1}.".format(oncall["start"],oncall["end"])
        }])
        slack_client.api_call(
                "chat.postMessage",
                channel = channel,
                attachments = attachments)
        return

    # Sends the response back to the channel
    slack_client.api_call(
        "chat.postMessage",
        channel=channel,
        text=response or default_response
    )

if __name__ == "__main__":
    if slack_client.rtm_connect(with_team_state=False):
        print("Starter Bot connected and running!")
        # Read bot's user ID by calling Web API method `auth.test`
        starterbot_id = slack_client.api_call("auth.test")["user_id"]
        oncall = get_oncall()
        while True:
            command, channel = parse_bot_commands(slack_client.rtm_read())
            if command:
                handle_command(command, channel)
            time.sleep(RTM_READ_DELAY)
    else:
        print("Connection failed. Exception traceback printed above.")

