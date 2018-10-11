# netops-slackbot

This code is loosely based on https://github.com/mattmakai/slack-starterbot and
code the Mozilla Operations Center was already using to interact with
PagerDuty.

To install:

* copy `config.yml.dist` to `config.yml`
* edit `config.yml` to suit. At a minimum you need:
** your Slack API key
** your PagerDuty API key
** the schedule ID and escalation policy ID that you wish to grab the oncall from (you need both for it to be accurate)
* `virtualenv env`
* `source env/bin/activate`
* `pip install -r requirements.txt`
