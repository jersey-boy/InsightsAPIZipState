# MCP Setup — AWS Documentation server

## What MCP is

**MCP (Model Context Protocol)** lets Kiro's AI agent connect to external "tool
servers" that extend what it can do during development. An MCP server is a small
program Kiro launches in the background; it exposes tools the agent can call
(e.g. "search the AWS docs"). MCP servers are a **development-time aid** — they
help us build and operate this project. They are **not** part of the deployed
Lambda runtime and have no effect on what the application does in production.

## What's configured here: AWS Documentation MCP

This workspace is configured to use the official **AWS Documentation MCP server**
(`awslabs.aws-documentation-mcp-server`). It lets the agent search and read
current AWS documentation on demand, rather than relying on general web search or
possibly-stale training knowledge.

Why it's useful for this project — the app leans on several AWS services, and
accurate, current docs matter:

- **Lambda** — limits (memory, timeout, `/tmp`), the AWS-managed pandas layer
  ARN/versions, packaging.
- **S3** — upload, presigned URLs, Block Public Access.
- **Secrets Manager** — `GetSecretValue`/`PutSecretValue`, rotation patterns.
- **SES** — sandbox vs. production, send limits, verified identities/DKIM.
- **IAM** — least-privilege policy syntax.
- **EventBridge Scheduler** — cron scheduling.

It is **read-only** (documentation lookup), so it carries no risk to live AWS
resources. It does not use your AWS credentials and cannot change infrastructure.

> Note: there is no official MCP server for the **NationBuilder** API (the actual
> data source), so the AWS docs server helps the AWS side of the project, not the
> NationBuilder side.

## Prerequisite: uv / uvx

The server runs via **`uvx`** (part of [`uv`](https://docs.astral.sh/uv/), a fast
Python package runner). `uvx` downloads and runs the server package on demand —
there is no separate "install the server" step.

`uv`/`uvx` were installed on this machine via the official standalone installer:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

This installs `uv.exe`/`uvx.exe` to `C:\Users\<you>\.local\bin` and adds that
directory to the user PATH (no admin required). Verify with:

```powershell
uvx --version
```

If Kiro reports `uvx` not found after a fresh install, fully restart Kiro so it
inherits the updated PATH.

## Configuration

Workspace-level MCP config lives at **`.kiro/settings/mcp.json`**:

```json
{
  "mcpServers": {
    "aws-docs": {
      "command": "uvx",
      "args": ["awslabs.aws-documentation-mcp-server@latest"],
      "env": { "FASTMCP_LOG_LEVEL": "ERROR" },
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

- **Workspace vs. user level:** this is workspace-level (shared with the project
  via `.kiro/settings/mcp.json`). A user-level equivalent would live at
  `~/.kiro/settings/mcp.json` and apply to all workspaces.
- **`disabled`** toggles the server on/off.
- **`autoApprove`** can list tool names to run without a confirmation prompt;
  left empty here so tool calls are confirmed.

## Managing the server in Kiro

- Edit the config via the command palette (search **MCP**) or by editing
  `.kiro/settings/mcp.json` directly. (The agent is not permitted to write MCP
  config files — this is a deliberate guardrail; a human edits them.)
- The **MCP Server view** in the Kiro feature panel lists servers and lets you
  reconnect after config changes without restarting Kiro.
- The first launch may take a moment while `uvx` downloads the server package.
