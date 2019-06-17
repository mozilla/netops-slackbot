#!/usr/bin/env python
import os
import time
import re
from slackclient import SlackClient
import yaml
import json
import requests

with open("config.yml", 'r') as ymlfile:
    cfg = yaml.safe_load(ymlfile)

# instantiate Slack client
slack_client = SlackClient(cfg['slack_api_token'])

# the bot's user ID in Slack: value is assigned after the bot starts up
bot_id = None

# oncall person object
oncall = cfg["default_oncall"]
channels = {}

# constants
RECONNECT_DELAY = 30 # how long to wait between retries when the connection fails
RTM_READ_DELAY = 1 # 1 second delay between reading from RTM
MENTION_REGEX = "^<@(|[WU].+?)>(.*)"
DEFAULT_CHANNEL = cfg["oncall_announce_channel"]
DEBUG = 0

def get_channels():
    channel_list = slack_client.api_call("users.conversations", types="public_channel,private_channel")
    if DEBUG:
        print("Fetching users.conversations:")
        print(json.dumps(channel_list, indent=4))
    global channels
    channels = {}
    for channelobj in channel_list['channels']:
        channels[channelobj['name']] = channelobj

def get_oncall():
    global oncall
    headers = {
        'Authorization': 'Token token={0}'.format(cfg["pagerduty_api_token"]),
        'Content-Type': 'application/vnd.pagerduty+json;version=2',
    }
    url = 'https://api.pagerduty.com/oncalls?time_zone=UTC&include%5B%5D=users&escalation_policy_ids%5B%5D={0}&schedule_ids%5B%5D={1}'.format(cfg["pagerduty_escalation_policy"],cfg["pagerduty_oncall_schedule"])
    try:
        r = requests.get(url, headers=headers, timeout=30)
        decoded = json.loads(r.text)
        oncall = decoded["oncalls"][0]["user"]
        oncall["start"] = decoded["oncalls"][0]["start"]
        oncall["end"] = decoded["oncalls"][0]["end"]
        # parse text of "description" field ("Bio" in the UI) to get IRC
        # and Slack if present, but fall back to first part of email if not given
        oncall["irc_nick"] = oncall["email"].split("@")[0]
        oncall["slack_nick"] = oncall["email"].split("@")[0]
        # check for an IRC nick in the Bio field
        if oncall["description"]:
            match = re.search(':(\S+)', oncall["description"])
            if match:
                oncall["irc_nick"] = match.group(1)
            # check for a Slack nick in the Bio field
            match = re.search('@(.+) on Slack', oncall["description"])
            if match:
                oncall["slack_nick"] = match.group(1)
    except requests.Timeout:
        print("PagerDuty API timed out!")
        pass
    except requests.exceptions.ConnectionError as e:
        print("PagerDuty API failed to connect: %s" % e)
        pass
    except Exception as e:
        print("Got an error looking up the oncall in PagerDuty: %s" % e)
        raise

    return

def parse_bot_commands(slack_events):
    """
        Parses a list of events coming from the Slack RTM API to find bot commands.
        If a bot command is found, this function returns a tuple of command and channel.
        If its not found, then this function returns None, None.
    """
    for event in slack_events:
        if event["type"] == "message" and not "subtype" in event:
            user_id, message = parse_direct_mention(event["text"])
            if user_id == bot_id:
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

def post_current_oncall(channel, pretext="The current oncall network engineer is:"):
    """
        Posts the current oncall information to the given channel
    """
    attachments = json.dumps([{
        "color": "#36a64f",
        "pretext": pretext,
        "title": oncall["name"],
        "title_link": oncall["html_url"],
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
    channel_info = slack_client.api_call(
            "conversations.info",
            channel = channel)
    if not channel_info["ok"]:
        print("Looking up channel: %s" % channel)
        print("Got an error from conversations.info: %s" % channel_info["error"])
        print(json.dumps(channel_info))
    else:
        print("Posting current oncall (%s) to #%s" % (oncall["email"], channel_info["channel"]["name"]))
        slack_client.api_call(
                "chat.postMessage",
                channel = channel,
                attachments = attachments)
        #slack_client.api_call(
        #        "conversations.setTopic",
        #        channel = channel,
        #        topic = "Current NetOps oncall is %s" % oncall["slack_nick"])
    return

def handle_command(command, channel):
    """
        Executes bot command if the command is known
    """
    # Default response is help text for the user
    default_response = "Not sure what you mean. Try 'oncall'"

    # Finds and executes the given command, filling in response
    response = None
    # Implement commands here!
    if command.startswith("oncall"):
        post_current_oncall(channel)
        return

    # Sends the response back to the channel if the command returned one
    # instead of exiting
    slack_client.api_call(
        "chat.postMessage",
        channel=channel,
        text=response or default_response
    )

if __name__ == "__main__":
    while True:
        try:
            print("Connecting to Slack...")
            connected = slack_client.rtm_connect(with_team_state=False, auto_reconnect=True)
            if connected:
                print("Netops Bot connected and running!")
                # Read bot's user ID by calling Web API method `auth.test`
                bot_id = slack_client.api_call("auth.test")["user_id"]
                last_oncall_check = 0
                state = { "current_oncall": "nobody" }
                get_channels()
                try:
                    with open("state.yml", 'r') as ymlfile:
                        try:
                            state = yaml.safe_load(ymlfile)
                        except:
                            state = { "current_oncall": "nobody" }
                            pass
                except IOError:
                    pass
                while True:
                    ts = time.time()
                    if (ts - last_oncall_check) > 60:
                        get_oncall()
                        last_oncall_check = ts
                        if (oncall["email"] != state['current_oncall']):
                            print("Oncall changed from %s to %s" % (state["current_oncall"], oncall["email"]))
                            post_current_oncall(channels[DEFAULT_CHANNEL]['id'],"The current oncall network engineer is now:")
                            state['current_oncall'] = oncall["email"]
                            with open('state.yml', 'w') as outfile:
                                yaml.safe_dump(state, outfile, default_flow_style=False)
                    command, channel = parse_bot_commands(slack_client.rtm_read())
                    if command:
                        handle_command(command, channel)
                    time.sleep(RTM_READ_DELAY)
            else:
                print("Connection failed.")
                print("Making another attempt in %d seconds..." % RECONNECT_DELAY)
                time.sleep(RECONNECT_DELAY)
        except requests.exceptions.ConnectionError as e:
            print("Connection disconnected: %s" % e)
            print("Re-connecting in %d seconds..." % RECONNECT_DELAY)
            time.sleep(RECONNECT_DELAY)
            pass
        except Exception as e:
            print("Connection disconnected: %s" % e)
            print("Re-connecting in %d seconds..." % RECONNECT_DELAY)
            time.sleep(RECONNECT_DELAY)
            pass
            #raise

