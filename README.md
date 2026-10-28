# eBOK Tarnów — Home Assistant integration

Custom integration for the Tarnowskie Wodociągi eBOK portal.

## Features

- Reads the current water-meter reading and its date.
- Reads recent-period consumption.
- Reads account balance and latest invoice/payment information.
- Optional import of historical meter readings into Home Assistant long-term statistics.

## Installation

### HACS (custom repository)

1. In Home Assistant, open **HACS → Integrations**.
2. Open the menu and choose **Custom repositories**.
3. Add `vvkor/ha-ebok-tw-tarnow` as an **Integration**.
4. Install **eBOK Tarnów** and restart Home Assistant.
5. Add the integration under **Settings → Devices & services → Add integration**.
6. Enter the eBOK portal URL and your own credentials.

Default portal URL: `https://ebok.tw.tarnow.pl`.

## Historical statistics

Historical import is optional and can be controlled in the integration options. It writes points to Home Assistant long-term statistics. Review this behavior carefully before enabling it on an installation with existing meter statistics: the history importer and the live `total_increasing` sensor currently share a statistic ID.

## Security and privacy

- Do not commit credentials, session cookies, tokens, account exports, or Home Assistant backups.
- Configure credentials through the Home Assistant integration flow.
- Logs should not contain passwords or account-specific page contents.

## Compatibility

The integration is developed against recent Home Assistant Core releases. It is a custom integration and is not part of Home Assistant Core.
