#!/usr/bin/env python
import os
import re
import yaml
import json
import requests
import slack
import ssl as ssl_lib
import certifi
import asyncio
import nest_asyncio
from aiohttp.client_exceptions import ClientHttpProxyError

# constants
DEBUG = 1
polling_initialized = 0
nest_asyncio.apply()

def get_oncall():
    """Make an API request to PagerDuty to fetch the current oncall person.
       Just updates the in-memory data structure.
    """
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

def post_current_oncall(web_client, channel, pretext="The current oncall network engineer is:"):
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
    print("Posting current oncall (%s) to #%s" % (oncall["email"], channel))
    web_client.chat_postMessage(
            channel = channel,
            attachments = attachments)
    #slack_client.api_call(
    #        "conversations.setTopic",
    #        channel = channel,
    #        topic = "Current NetOps oncall is %s" % oncall["slack_nick"])
    return

def poll_pagerduty(**payload):
    global cfg
    global oncall
    global state
    global slack_web_client
    global event_loop
    print("poll_pagerduty got called")
    event_loop = asyncio.get_event_loop()
    event_loop.call_later(60, poll_pagerduty)
    get_oncall()
    if (oncall['email'] != state['current_oncall']):
        print("Oncall changed from %s to %s" % (state["current_oncall"], oncall["email"]))
        post_current_oncall(slack_web_client, cfg["oncall_announce_channel"], "The current oncall network engineer is now:")
        state['current_oncall'] = oncall['email']
        with open('state.yml', 'w') as outfile:
            yaml.safe_dump(state, outfile, default_flow_style=False)

@slack.RTMClient.run_on(event="hello")
def rtm_init(**payload):
    """grab some stuff from RTMClient when it starts so our scheduled actions
       can make use of it.
    """
    global slack_web_client
    global event_loop
    global broadcast_channel
    global polling_initialized
    print("rtm_init got run")
    slack_web_client = payload["web_client"]
    if not polling_initialized:
        event_loop.call_soon(poll_pagerduty)
        polling_initialized = 1
    #print("calling conversations_info")
    #channel_info = slack_web_client.conversations_info(channel = cfg['oncall_announce_channel'])
    #print("conversations_info called")
    #print(json.dumps(channel_info))
    #if not channel_info["ok"]:
    #    print("Looking up channel: %s" % channel)
    #    print("Got an error from conversations.info: %s" % channel_info["error"])

@slack.RTMClient.run_on(event="message")
def message(**payload):
    """process channel messages
    """
    print("message received");
    data = payload["data"]
    web_client = payload["web_client"]
    channel_id = data.get("channel")
    user_id = data.get("user")
    text = data.get("text")

    if text and text.lower() == cfg['prefix_char'] + "oncall":
        return post_current_oncall(web_client, channel_id)

if __name__ == "__main__":
    global cfg
    global oncall
    global state
    global event_loop
    with open("config.yml", 'r') as ymlfile:
        cfg = yaml.safe_load(ymlfile)
    oncall = cfg["default_oncall"]
    ssl_context = ssl_lib.create_default_context(cafile=certifi.where())
    state = { "current_oncall": "nobody" }
    try:
        with open("state.yml", 'r') as ymlfile:
            try:
                state = yaml.safe_load(ymlfile)
            except:
                state = { "current_oncall": "nobody" }
                pass
    except IOError:
        pass
    event_loop = asyncio.get_event_loop()
    proxy = None
    try:
        proxy = os.environ["HTTPS_PROXY"]
        proxy = "http://" + proxy
        print("Using proxy: %s" % proxy)
    except:
        proxy = None
    rtm_client = slack.RTMClient(
        token=cfg['slack_api_token'],
        ssl=ssl_context,
        proxy=proxy,
        loop=event_loop,
        connect_method='rtm.start'
    )
    print("Starting rtm_client")
    try:
        rtm_client.start()
        print("")
        print("rtm_client exited")
    except ClientHttpProxyError as e:
        print("rtm_client exited with an error: %s" % e)
        print("Requested URL: %s" % e.request_info.url)
    except Exception as e:
        print("rtm_client exited with an error: %s" % e)
        raise
