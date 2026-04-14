# lark-cli-lig

LIG Nutrition's Lark (Feishu) CLI -- messaging, approvals, contacts, bitable, and raw API access.

## Install

```bash
pip install lark-cli-lig
```

## Setup

Create a `.env` file in your working directory (or export the variables):

```
LARK_APP_ID=cli-xxxxxxxx
LARK_APP_SECRET=xxxxxxxx
LARK_DOMAIN=https://open.larksuite.com   # or https://open.feishu.cn
```

## Commands

| Command | Description |
|---|---|
| `lark-lig send` | Send a text message to a user (by email or open_id) |
| `lark-lig send-image` | Send an image (png/jpg) to a user |
| `lark-lig send-file` | Send a file (pdf/doc/xls...) to a user |
| `lark-lig send-group` | Send a text message to a group chat |
| `lark-lig read` | Read messages from a chat |
| `lark-lig users` | List root department users |
| `lark-lig users-all` | List all org users across departments |
| `lark-lig chats` | List bot's group chats |
| `lark-lig auth login` | Login via OAuth (opens browser) |
| `lark-lig auth status` | Show current auth status |
| `lark-lig auth logout` | Remove stored tokens |
| `lark-lig approval types` | List approval definitions |
| `lark-lig approval list` | List submitted approvals |
| `lark-lig approval get` | Get approval instance details |
| `lark-lig approval upload` | Upload an image for approval (PDF auto-convert) |
| `lark-lig approval submit` | Submit an approval instance from JSON |
| `lark-lig api` | Call any Lark Open API endpoint directly |

## Features

- **Identity switching** -- run as `--as user` (OAuth) or `--as bot` (tenant token, default)
- **Verbose logging** -- `-v` for summary, `-vv` for full debug output
- **Keyring token storage** -- OAuth tokens stored securely via system keyring
- **PDF auto-conversion** -- approval image upload auto-converts PDF pages to images
- **Raw API access** -- `lark-lig api` lets you call any Lark endpoint directly

## Requirements

- Python 3.11+
- A Lark/Feishu custom app with App ID and App Secret
- For PDF conversion: `poppler` (`brew install poppler` on macOS)

## License

MIT
