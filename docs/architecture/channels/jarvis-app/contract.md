<!-- GENERATED FILE. Run `python scripts/export_contract.py` to refresh;
     `--check` verifies it is current (used in CI). Never hand-edited. -->

# jarvis-app wire contract

JSON Schema plus the endpoint list, generated from the Pydantic models and
the mounted routes under `backend/jarvis_app_backend`.

`contract_version`: `509c222e84e2c915`

## Endpoints

- `GET /bot/v1/attachments/{attachment_id}` — Bot Download Attachment
- `GET /bot/v1/updates` — Get Updates
- `GET /v1/apps` — Get Apps
- `GET /v1/apps/{ns}/q/{entry_id}` — Query App
- `GET /v1/attachments/{attachment_id}` — Download Attachment
- `GET /v1/commands` — Get Commands
- `GET /v1/events` — Events
- `GET /v1/health` — Health
- `GET /v1/messages` — Get Messages
- `PATCH /bot/v1/messages/{message_id}` — Bot Patch
- `POST /bot/v1/apps` — Declare Apps
- `POST /bot/v1/apps/{query_id}/results` — Post App Query Results
- `POST /bot/v1/attachments` — Bot Upload Attachment
- `POST /bot/v1/commands` — Declare Commands
- `POST /bot/v1/events` — Bot Event
- `POST /bot/v1/messages` — Bot Send
- `POST /v1/actions` — Post Action
- `POST /v1/apps/{ns}/q/{entry_id}` — Query App Post
- `POST /v1/attachments` — Upload Attachment
- `POST /v1/auth/login` — Login
- `POST /v1/auth/logout` — Logout
- `POST /v1/messages` — Send Message
- `POST /v1/push/register` — Register Push

## Models

### ActionRequest

```json
{
  "additionalProperties": false,
  "description": "`POST /v1/actions`'s body \u2014 a tap on a live block. `action_id` names\none of the block's declared affordances, or, for `confirmation` (which\ndeclares none of its own \u2014 it has exactly two outcomes), the reserved\n`\"confirm\"`/`\"cancel\"` (architecture \u00a75).\n\n`values` carries what was typed into a `form`, and only a `form`: a kind\nthat declares no fields may not send it at all. Every declared `field_id`\nmust be present and `null` stands for a box left empty, so the agent can\ntell *\"seen and left blank\"* from *\"the app dropped a field\"* \u2014 an\nambiguity that would otherwise have no answer.\n\n`int` sits in the union ahead of `float` so a count stays a count: a reps\nbox holding `8` reaches the agent as `8`, not `8.0`, which it would if\nevery number widened to the only numeric type on offer. **A `bool` is not\nrefused, it is coerced** \u2014 Python's `bool` subclasses `int`, so `true`\narrives as `1`. Nothing this app ships can send one (no field type\ncollects a boolean), and refusing it would mean hand-written validation\nguarding against a value our own client cannot produce; it is recorded\nhere rather than defended against.",
  "properties": {
    "action_id": {
      "title": "Action Id",
      "type": "string"
    },
    "message_id": {
      "title": "Message Id",
      "type": "integer"
    },
    "values": {
      "anyOf": [
        {
          "additionalProperties": {
            "anyOf": [
              {
                "type": "string"
              },
              {
                "type": "integer"
              },
              {
                "type": "number"
              },
              {
                "type": "null"
              }
            ]
          },
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Values"
    }
  },
  "required": [
    "message_id",
    "action_id"
  ],
  "title": "ActionRequest",
  "type": "object"
}
```

### ActionUpdate

```json
{
  "description": "A tap the hub already validated against the block it names. `callback_id`\nis `None` when the block that was tapped carries none of its own.\n\n`values` is what was typed into a `form`, relayed unchanged and `None` for\nevery other kind \u2014 the hub refuses a tap that carries it on a kind with no\nfields, so its presence here already means the block declared them. Every\ndeclared `field_id` is present; a `null` is a box the user saw and left\nempty, which is why the agent never has to guess whether a missing key\nmeans blank or lost.",
  "properties": {
    "action_id": {
      "title": "Action Id",
      "type": "string"
    },
    "block_kind": {
      "title": "Block Kind",
      "type": "string"
    },
    "callback_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Callback Id"
    },
    "message_id": {
      "title": "Message Id",
      "type": "integer"
    },
    "type": {
      "const": "action",
      "title": "Type",
      "type": "string"
    },
    "update_id": {
      "title": "Update Id",
      "type": "integer"
    },
    "values": {
      "anyOf": [
        {
          "additionalProperties": {
            "anyOf": [
              {
                "type": "string"
              },
              {
                "type": "integer"
              },
              {
                "type": "number"
              },
              {
                "type": "null"
              }
            ]
          },
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Values"
    }
  },
  "required": [
    "update_id",
    "type",
    "message_id",
    "action_id",
    "block_kind"
  ],
  "title": "ActionUpdate",
  "type": "object"
}
```

### ApiError

```json
{
  "$defs": {
    "ApiErrorBody": {
      "description": "The object nested under `\"error\"` \u2014 the part of the envelope a client\nactually branches on.",
      "properties": {
        "code": {
          "title": "Code",
          "type": "string"
        },
        "detail": {
          "anyOf": [
            {
              "additionalProperties": true,
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Detail"
        },
        "message": {
          "title": "Message",
          "type": "string"
        },
        "retry_after_s": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Retry After S"
        }
      },
      "required": [
        "code",
        "message"
      ],
      "title": "ApiErrorBody",
      "type": "object"
    }
  },
  "description": "The uniform envelope every non-2xx response uses (architecture \u00a75,\nConventions). Documented here, next to `_error_response`, which is what\nactually builds this shape by hand below \u2014 so `CONTRACT.md` describes the\nreal runtime body rather than a second, hand-maintained copy of it.",
  "properties": {
    "error": {
      "$ref": "#/$defs/ApiErrorBody"
    }
  },
  "required": [
    "error"
  ],
  "title": "ApiError",
  "type": "object"
}
```

### AppEntry

```json
{
  "additionalProperties": false,
  "description": "One callable entry point on an app.\n\n`id` is a name the **agent** routes on \u2014 never a path the hub dials. The hub\nholds no agent URL and no way to acquire one (architecture \u00a76), so an entry\nthat read as a path would be describing a call nobody can make.\n\n`method` is per entry rather than per app so a read and a write can live\nside by side under one namespace. It is the hub's only way to know that a\nrequest is safe, which is what lets a write be refused here rather than a\nround-trip later.",
  "properties": {
    "id": {
      "pattern": "^[a-z][a-z0-9_]{0,31}$",
      "title": "Id",
      "type": "string"
    },
    "method": {
      "enum": [
        "GET",
        "POST"
      ],
      "title": "Method",
      "type": "string"
    },
    "params": {
      "items": {
        "pattern": "^[a-z][a-z0-9_]{0,31}$",
        "type": "string"
      },
      "title": "Params",
      "type": "array"
    }
  },
  "required": [
    "id",
    "method"
  ],
  "title": "AppEntry",
  "type": "object"
}
```

### AppManifest

```json
{
  "$defs": {
    "AppEntry": {
      "additionalProperties": false,
      "description": "One callable entry point on an app.\n\n`id` is a name the **agent** routes on \u2014 never a path the hub dials. The hub\nholds no agent URL and no way to acquire one (architecture \u00a76), so an entry\nthat read as a path would be describing a call nobody can make.\n\n`method` is per entry rather than per app so a read and a write can live\nside by side under one namespace. It is the hub's only way to know that a\nrequest is safe, which is what lets a write be refused here rather than a\nround-trip later.",
      "properties": {
        "id": {
          "pattern": "^[a-z][a-z0-9_]{0,31}$",
          "title": "Id",
          "type": "string"
        },
        "method": {
          "enum": [
            "GET",
            "POST"
          ],
          "title": "Method",
          "type": "string"
        },
        "params": {
          "items": {
            "pattern": "^[a-z][a-z0-9_]{0,31}$",
            "type": "string"
          },
          "title": "Params",
          "type": "array"
        }
      },
      "required": [
        "id",
        "method"
      ],
      "title": "AppEntry",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "description": "One app, as the agent publishes it.\n\nAn app with no entries is refused: nothing about it could ever be called, so\nit would draw an icon that cannot lead anywhere \u2014 the dead affordance the\nclient is built to avoid rather than render.",
  "properties": {
    "entries": {
      "items": {
        "$ref": "#/$defs/AppEntry"
      },
      "minItems": 1,
      "title": "Entries",
      "type": "array"
    },
    "name": {
      "maxLength": 64,
      "minLength": 1,
      "title": "Name",
      "type": "string"
    },
    "ns": {
      "pattern": "^[a-z][a-z0-9_]{0,31}$",
      "title": "Ns",
      "type": "string"
    }
  },
  "required": [
    "ns",
    "name",
    "entries"
  ],
  "title": "AppManifest",
  "type": "object"
}
```

### AppQueryError

```json
{
  "additionalProperties": false,
  "description": "An app-level failure, as the agent reports it.\n\n`code` is a **closed** vocabulary the hub maps to a status. It is closed\nrather than passed through because passing it through would hand the agent\ncontrol of the hub's own status codes \u2014 including the ones a client\nbranches on, like `401`. An unrecognised code still produces an answer, but\na `502`: the agent replied with something the hub cannot honour.",
  "properties": {
    "code": {
      "title": "Code",
      "type": "string"
    },
    "message": {
      "title": "Message",
      "type": "string"
    }
  },
  "required": [
    "code",
    "message"
  ],
  "title": "AppQueryError",
  "type": "object"
}
```

### AppQueryResult

```json
{
  "$defs": {
    "AppQueryError": {
      "additionalProperties": false,
      "description": "An app-level failure, as the agent reports it.\n\n`code` is a **closed** vocabulary the hub maps to a status. It is closed\nrather than passed through because passing it through would hand the agent\ncontrol of the hub's own status codes \u2014 including the ones a client\nbranches on, like `401`. An unrecognised code still produces an answer, but\na `502`: the agent replied with something the hub cannot honour.",
      "properties": {
        "code": {
          "title": "Code",
          "type": "string"
        },
        "message": {
          "title": "Message",
          "type": "string"
        }
      },
      "required": [
        "code",
        "message"
      ],
      "title": "AppQueryError",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "description": "What the agent posts back for a parked query: a payload, or a failure.\n\nExactly one of `data`/`error`, and the discriminator has to be in the body\nbecause this leg has no status code of its own to carry it \u2014 the agent is\nPOSTing an answer, so the HTTP result describes whether the *hub accepted\nthe answer*, not whether the query succeeded. The client's leg needs no such\nwrapper: there a status code exists, so `data` is returned raw.\n\n`data` is `Any`, and deliberately un-modelled. The hub relays an app's\npayload without interpreting it; a model here would mean the hub knowing\nwhat each app's data looks like, which is the coupling this whole path\nexists to avoid.",
  "properties": {
    "data": {
      "anyOf": [
        {},
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Data"
    },
    "error": {
      "anyOf": [
        {
          "$ref": "#/$defs/AppQueryError"
        },
        {
          "type": "null"
        }
      ],
      "default": null
    }
  },
  "title": "AppQueryResult",
  "type": "object"
}
```

### AppQueryUpdate

```json
{
  "description": "A client asking an app for data, relayed to the agent that owns it.\n\nUnlike the other variants this one has somebody waiting on it: a client\nrequest is parked on the hub until `POST /bot/v1/apps/{query_id}/results`\nanswers it, and it is abandoned after a timeout. So it is the one update\nkind that goes stale \u2014 an agent that answers a `query_id` nobody is holding\nis told so rather than silently succeeding.\n\nIt must therefore be answered promptly, and never behind whatever else the\nagent is doing: it names no thread and needs no model, so an agent that\nserialises it behind a conversational turn turns a data read into a wait of\nthat turn's length.\n\n`params` is only what the app's own manifest declared. The **values** are\nuninterpreted \u2014 the hub bounds their length and nothing else, because\njudging one would mean knowing what the app means by it. Whatever they\naddress, the agent validates.",
  "properties": {
    "entry_id": {
      "title": "Entry Id",
      "type": "string"
    },
    "ns": {
      "title": "Ns",
      "type": "string"
    },
    "params": {
      "additionalProperties": {
        "type": "string"
      },
      "title": "Params",
      "type": "object"
    },
    "query_id": {
      "title": "Query Id",
      "type": "string"
    },
    "type": {
      "const": "app_query",
      "title": "Type",
      "type": "string"
    },
    "update_id": {
      "title": "Update Id",
      "type": "integer"
    }
  },
  "required": [
    "update_id",
    "type",
    "query_id",
    "ns",
    "entry_id",
    "params"
  ],
  "title": "AppQueryUpdate",
  "type": "object"
}
```

### Attachment

```json
{
  "additionalProperties": false,
  "description": "One uploaded blob, as it rides `attachments[]` on a `Message` or the\nresponse of `POST /v1/attachments`.",
  "properties": {
    "blur_preview": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Blur Preview"
    },
    "duration_ms": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Duration Ms"
    },
    "filename": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Filename"
    },
    "height": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Height"
    },
    "id": {
      "pattern": "^att_[0-9A-HJKMNP-TV-Z]{26}$",
      "title": "Id",
      "type": "string"
    },
    "kind": {
      "enum": [
        "image",
        "audio",
        "file"
      ],
      "title": "Kind",
      "type": "string"
    },
    "mime_type": {
      "title": "Mime Type",
      "type": "string"
    },
    "size": {
      "title": "Size",
      "type": "integer"
    },
    "width": {
      "anyOf": [
        {
          "type": "integer"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Width"
    }
  },
  "required": [
    "id",
    "kind",
    "mime_type",
    "size"
  ],
  "title": "Attachment",
  "type": "object"
}
```

### BotEvent

```json
{
  "additionalProperties": false,
  "description": "An ephemeral event the agent relays to the client's stream \u2014 a tool chip,\na stream delta, or the \"thinking\" heartbeat (architecture \u00a75 SSE catalog).\nNever persisted and carries no cursor, so the stream frames it without an\n`id:` line.\n\n`type` is strict: an unknown one is a 422, not a mystery event relayed to the\nphone. It admits the whole catalog, not only the two tool-chip kinds a first\nagent sends \u2014 the agent, not the hub, chooses which to emit, and each is a\nworking relay end to end. `data` is opaque: the hub carries the chip's shape,\nit does not interpret it \u2014 `agent_thinking`'s own `ttl_ms` included.",
  "properties": {
    "data": {
      "additionalProperties": true,
      "title": "Data",
      "type": "object"
    },
    "type": {
      "enum": [
        "tool_call_started",
        "tool_call_result",
        "agent_stream_delta",
        "agent_message_final",
        "agent_thinking"
      ],
      "title": "Type",
      "type": "string"
    }
  },
  "required": [
    "type",
    "data"
  ],
  "title": "BotEvent",
  "type": "object"
}
```

### BotMessageMeta

```json
{
  "additionalProperties": false,
  "description": "A bot cannot author a message that claims to come from the user: its\n`source` is one of the four agent-side origins, never `\"user\"` \u2014 that value\nis the client send path's, hardcoded there. Narrowing `MessageMeta`'s\n`source` makes the incoherent `assistant` + `source: \"user\"` row\nunrepresentable rather than a rule to remember.",
  "properties": {
    "source": {
      "default": "agent",
      "enum": [
        "agent",
        "heartbeat",
        "reminder",
        "notifier"
      ],
      "title": "Source",
      "type": "string"
    }
  },
  "title": "BotMessageMeta",
  "type": "object"
}
```

### BotPatchRequest

```json
{
  "additionalProperties": false,
  "description": "Resolves a message's one block by setting its `state` alone \u2014 how the\nagent answers a `confirmation` (`confirmed`/`cancelled`/`expired`) or\nnames a `buttons` selection. The body carries no block: `message_id` in\nthe URL plus \"at most one interactive block per message\" (architecture\n\u00a75) is already an unambiguous address, and there is no content for the\nagent to send even if it wanted to \u2014 an action update carries none, and\nthe Bot API has no read.\n\nThis model cannot enforce the terminal-state rule itself \u2014 it has no way\nto see what is already stored, only what was sent. That guard, and the\nper-kind check on `state`'s own vocabulary, both live in\n`messages.db.update_message_blocks`, the only place that holds the\nstored block to check against: once a stored block's `state` is\nnon-null, a PATCH may change it only to the same value again.",
  "properties": {
    "state": {
      "title": "State",
      "type": "string"
    }
  },
  "required": [
    "state"
  ],
  "title": "BotPatchRequest",
  "type": "object"
}
```

### BotSendRequest

```json
{
  "$defs": {
    "Action": {
      "additionalProperties": false,
      "description": "A tappable option: `card.actions` and `buttons.options` both use this\nshape, so a card's own buttons and a bare `buttons` block behave the same\nway once tapped.",
      "properties": {
        "action_id": {
          "title": "Action Id",
          "type": "string"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "action_id",
        "label"
      ],
      "title": "Action",
      "type": "object"
    },
    "BotMessageMeta": {
      "additionalProperties": false,
      "description": "A bot cannot author a message that claims to come from the user: its\n`source` is one of the four agent-side origins, never `\"user\"` \u2014 that value\nis the client send path's, hardcoded there. Narrowing `MessageMeta`'s\n`source` makes the incoherent `assistant` + `source: \"user\"` row\nunrepresentable rather than a rule to remember.",
      "properties": {
        "source": {
          "default": "agent",
          "enum": [
            "agent",
            "heartbeat",
            "reminder",
            "notifier"
          ],
          "title": "Source",
          "type": "string"
        }
      },
      "title": "BotMessageMeta",
      "type": "object"
    },
    "ButtonsBlock": {
      "additionalProperties": false,
      "description": "`state` is the selected `action_id` once resolved \u2014 an open string\nrather than an enum, because the values are whatever the author of this\nblock's `options` chose.",
      "properties": {
        "kind": {
          "const": "buttons",
          "default": "buttons",
          "title": "Kind",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/ButtonsPayload"
        },
        "state": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "summary": {
          "title": "Summary",
          "type": "string"
        }
      },
      "required": [
        "summary",
        "payload"
      ],
      "title": "ButtonsBlock",
      "type": "object"
    },
    "ButtonsPayload": {
      "additionalProperties": false,
      "description": "No prose field exists here \u2014 see the module docstring. `options`\nabsorbs what a separate `choice` kind would otherwise do.",
      "properties": {
        "options": {
          "items": {
            "$ref": "#/$defs/Action"
          },
          "title": "Options",
          "type": "array"
        }
      },
      "required": [
        "options"
      ],
      "title": "ButtonsPayload",
      "type": "object"
    },
    "CardBlock": {
      "additionalProperties": false,
      "properties": {
        "kind": {
          "const": "card",
          "default": "card",
          "title": "Kind",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/CardPayload"
        },
        "state": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "summary": {
          "title": "Summary",
          "type": "string"
        }
      },
      "required": [
        "summary",
        "payload"
      ],
      "title": "CardBlock",
      "type": "object"
    },
    "CardPayload": {
      "additionalProperties": false,
      "properties": {
        "actions": {
          "items": {
            "$ref": "#/$defs/Action"
          },
          "title": "Actions",
          "type": "array"
        },
        "body": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Body"
        },
        "subtitle": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subtitle"
        },
        "title": {
          "title": "Title",
          "type": "string"
        }
      },
      "required": [
        "title"
      ],
      "title": "CardPayload",
      "type": "object"
    },
    "ConfirmationBlock": {
      "additionalProperties": false,
      "properties": {
        "kind": {
          "const": "confirmation",
          "default": "confirmation",
          "title": "Kind",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/ConfirmationPayload"
        },
        "state": {
          "anyOf": [
            {
              "enum": [
                "confirmed",
                "cancelled",
                "expired"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "summary": {
          "title": "Summary",
          "type": "string"
        }
      },
      "required": [
        "summary",
        "payload"
      ],
      "title": "ConfirmationBlock",
      "type": "object"
    },
    "ConfirmationPayload": {
      "additionalProperties": false,
      "description": "`body` carries the question this block asks \u2014 required, since nothing\nelse on the block carries it. `title` is optional and renders no row at\nall when absent; it exists for the rarer confirmation whose question\nneeds a heading above detail. The two labels are what vary the button\nwording between one confirmation and another.",
      "properties": {
        "body": {
          "title": "Body",
          "type": "string"
        },
        "callback_id": {
          "title": "Callback Id",
          "type": "string"
        },
        "cancel_label": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Cancel Label"
        },
        "confirm_label": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Confirm Label"
        },
        "title": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Title"
        }
      },
      "required": [
        "callback_id",
        "body"
      ],
      "title": "ConfirmationPayload",
      "type": "object"
    },
    "FormBlock": {
      "additionalProperties": false,
      "description": "`state` narrows to two values, not three: a form has nothing to decline,\nso there is no `cancelled` to sit beside `logged`. `expired` still applies \u2014\nthat is the absence of a decision, which a form can have like any other\nkind.\n\n**`values` is the one field in this contract a *client* writes onto a row\nthe agent authored**, and it is what makes a resolved form a record of what\nwas submitted rather than an echo of what was proposed. Everything else\nhere came from the agent's send; `state` is agent-written too, through\n`PATCH`, even though a tap is what triggers it. This arrives on\n`POST /v1/actions`, is validated against the fields `payload` declares, and\nis stamped by the hub in that same transaction.\n\nIt sits here rather than inside `FormPayload` because the payload is what\nthe agent proposed, and merging the user's own work into it would erase the\none distinction the field exists to make.\n\n**The send route refuses it, and that refusal is load-bearing rather than\ntidy.** `values` is *evidence of what the user submitted*, and evidence an\nagent can write is not evidence \u2014 without the refusal an agent could send a\nform pre-stamped with a submission that never happened, and a client would\nrender it under the word `logged`. Declared optional here because a form is\nsent without it and carries it only after a tap; refused at the door\nrather than by this type, since Pydantic cannot see which direction a\nblock is travelling.",
      "properties": {
        "kind": {
          "const": "form",
          "default": "form",
          "title": "Kind",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/FormPayload"
        },
        "state": {
          "anyOf": [
            {
              "enum": [
                "logged",
                "expired"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "summary": {
          "title": "Summary",
          "type": "string"
        },
        "values": {
          "anyOf": [
            {
              "additionalProperties": {
                "anyOf": [
                  {
                    "type": "string"
                  },
                  {
                    "type": "integer"
                  },
                  {
                    "type": "number"
                  },
                  {
                    "type": "null"
                  }
                ]
              },
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Values"
        }
      },
      "required": [
        "summary",
        "payload"
      ],
      "title": "FormBlock",
      "type": "object"
    },
    "FormPayload": {
      "additionalProperties": false,
      "description": "`type` opens at `text` and `number` alone \u2014 the two a renderer draws.\nThe wider set a form could plausibly want (a choice, a toggle, a date, a\ntime) is deliberately absent rather than declared and unhandled: a type\nnothing can draw is a promise the system cannot keep, and each of those\nneeds a design before it needs a schema.\n\n`callback_id` sits here because the hub stamps it onto every action update,\nwhich is what lets an agent resolve the right pending decision without\nkeeping a `message_id`\u2192handle map it would lose on each deploy.\n\nA form declares no actions. It has exactly one, so naming it would be a\nsecond name for the same thing, and its tap carries the reserved\n`\"submit\"` \u2014 the same shape `confirmation` uses for its own two fixed\noutcomes. `submit_label` is the words on that one control, nothing more.",
      "properties": {
        "callback_id": {
          "title": "Callback Id",
          "type": "string"
        },
        "rows": {
          "items": {
            "$ref": "#/$defs/FormRow"
          },
          "title": "Rows",
          "type": "array"
        },
        "submit_label": {
          "default": "Submit",
          "title": "Submit Label",
          "type": "string"
        },
        "subtitle": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subtitle"
        },
        "title": {
          "title": "Title",
          "type": "string"
        }
      },
      "required": [
        "callback_id",
        "title",
        "rows"
      ],
      "title": "FormPayload",
      "type": "object"
    },
    "FormRow": {
      "additionalProperties": false,
      "description": "One labelled thing being filled in, and the one or two boxes it takes \u2014\n*\"Bench press\"* with a reps box and a kg box beside it. The grouping lives\nhere rather than being inferred from a flat list because the resolved and\nexpired renders collapse a **row** to a single value, so it has to exist in\nthe payload rather than be re-derived at draw time. A flat list carrying a\n`group` key was rejected for the reason the union above exists: the\ngrouping would become a convention the hub cannot check.",
      "properties": {
        "fields": {
          "items": {
            "discriminator": {
              "mapping": {
                "number": "#/$defs/NumberField",
                "text": "#/$defs/TextField"
              },
              "propertyName": "type"
            },
            "oneOf": [
              {
                "$ref": "#/$defs/TextField"
              },
              {
                "$ref": "#/$defs/NumberField"
              }
            ]
          },
          "minItems": 1,
          "title": "Fields",
          "type": "array"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "label",
        "fields"
      ],
      "title": "FormRow",
      "type": "object"
    },
    "NumberField": {
      "additionalProperties": false,
      "description": "A numeric box. Identical to `TextField` but for what `default` may hold,\nwhich is the whole reason these are two models rather than one with a\n`type` tag beside an untyped default.",
      "properties": {
        "default": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Default"
        },
        "field_id": {
          "title": "Field Id",
          "type": "string"
        },
        "type": {
          "const": "number",
          "default": "number",
          "title": "Type",
          "type": "string"
        },
        "unit": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Unit"
        }
      },
      "required": [
        "field_id"
      ],
      "title": "NumberField",
      "type": "object"
    },
    "TextField": {
      "additionalProperties": false,
      "description": "A free-text box. `default` is the *prepopulation*, not a hint: a form\narrives with the agent's best guess already in the box, to be corrected or\naccepted rather than composed from nothing. There is no placeholder \u2014 that\nrenders only into an empty box, which is a state nothing draws \u2014 and no\nper-field label, because `unit` is what names a box.",
      "properties": {
        "default": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Default"
        },
        "field_id": {
          "title": "Field Id",
          "type": "string"
        },
        "type": {
          "const": "text",
          "default": "text",
          "title": "Type",
          "type": "string"
        },
        "unit": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Unit"
        }
      },
      "required": [
        "field_id"
      ],
      "title": "TextField",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "properties": {
    "attachment_ids": {
      "items": {
        "pattern": "^att_[0-9A-HJKMNP-TV-Z]{26}$",
        "type": "string"
      },
      "title": "Attachment Ids",
      "type": "array"
    },
    "blocks": {
      "anyOf": [
        {
          "items": {
            "discriminator": {
              "mapping": {
                "buttons": "#/$defs/ButtonsBlock",
                "card": "#/$defs/CardBlock",
                "confirmation": "#/$defs/ConfirmationBlock",
                "form": "#/$defs/FormBlock"
              },
              "propertyName": "kind"
            },
            "oneOf": [
              {
                "$ref": "#/$defs/CardBlock"
              },
              {
                "$ref": "#/$defs/FormBlock"
              },
              {
                "$ref": "#/$defs/ButtonsBlock"
              },
              {
                "$ref": "#/$defs/ConfirmationBlock"
              }
            ]
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Blocks"
    },
    "meta": {
      "$ref": "#/$defs/BotMessageMeta"
    },
    "text": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Text"
    }
  },
  "title": "BotSendRequest",
  "type": "object"
}
```

### ButtonsBlock

```json
{
  "$defs": {
    "Action": {
      "additionalProperties": false,
      "description": "A tappable option: `card.actions` and `buttons.options` both use this\nshape, so a card's own buttons and a bare `buttons` block behave the same\nway once tapped.",
      "properties": {
        "action_id": {
          "title": "Action Id",
          "type": "string"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "action_id",
        "label"
      ],
      "title": "Action",
      "type": "object"
    },
    "ButtonsPayload": {
      "additionalProperties": false,
      "description": "No prose field exists here \u2014 see the module docstring. `options`\nabsorbs what a separate `choice` kind would otherwise do.",
      "properties": {
        "options": {
          "items": {
            "$ref": "#/$defs/Action"
          },
          "title": "Options",
          "type": "array"
        }
      },
      "required": [
        "options"
      ],
      "title": "ButtonsPayload",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "description": "`state` is the selected `action_id` once resolved \u2014 an open string\nrather than an enum, because the values are whatever the author of this\nblock's `options` chose.",
  "properties": {
    "kind": {
      "const": "buttons",
      "default": "buttons",
      "title": "Kind",
      "type": "string"
    },
    "payload": {
      "$ref": "#/$defs/ButtonsPayload"
    },
    "state": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "State"
    },
    "summary": {
      "title": "Summary",
      "type": "string"
    }
  },
  "required": [
    "summary",
    "payload"
  ],
  "title": "ButtonsBlock",
  "type": "object"
}
```

### CardBlock

```json
{
  "$defs": {
    "Action": {
      "additionalProperties": false,
      "description": "A tappable option: `card.actions` and `buttons.options` both use this\nshape, so a card's own buttons and a bare `buttons` block behave the same\nway once tapped.",
      "properties": {
        "action_id": {
          "title": "Action Id",
          "type": "string"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "action_id",
        "label"
      ],
      "title": "Action",
      "type": "object"
    },
    "CardPayload": {
      "additionalProperties": false,
      "properties": {
        "actions": {
          "items": {
            "$ref": "#/$defs/Action"
          },
          "title": "Actions",
          "type": "array"
        },
        "body": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Body"
        },
        "subtitle": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subtitle"
        },
        "title": {
          "title": "Title",
          "type": "string"
        }
      },
      "required": [
        "title"
      ],
      "title": "CardPayload",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "properties": {
    "kind": {
      "const": "card",
      "default": "card",
      "title": "Kind",
      "type": "string"
    },
    "payload": {
      "$ref": "#/$defs/CardPayload"
    },
    "state": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "State"
    },
    "summary": {
      "title": "Summary",
      "type": "string"
    }
  },
  "required": [
    "summary",
    "payload"
  ],
  "title": "CardBlock",
  "type": "object"
}
```

### Command

```json
{
  "additionalProperties": false,
  "properties": {
    "description": {
      "title": "Description",
      "type": "string"
    },
    "name": {
      "title": "Name",
      "type": "string"
    }
  },
  "required": [
    "name",
    "description"
  ],
  "title": "Command",
  "type": "object"
}
```

### ConfirmationBlock

```json
{
  "$defs": {
    "ConfirmationPayload": {
      "additionalProperties": false,
      "description": "`body` carries the question this block asks \u2014 required, since nothing\nelse on the block carries it. `title` is optional and renders no row at\nall when absent; it exists for the rarer confirmation whose question\nneeds a heading above detail. The two labels are what vary the button\nwording between one confirmation and another.",
      "properties": {
        "body": {
          "title": "Body",
          "type": "string"
        },
        "callback_id": {
          "title": "Callback Id",
          "type": "string"
        },
        "cancel_label": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Cancel Label"
        },
        "confirm_label": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Confirm Label"
        },
        "title": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Title"
        }
      },
      "required": [
        "callback_id",
        "body"
      ],
      "title": "ConfirmationPayload",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "properties": {
    "kind": {
      "const": "confirmation",
      "default": "confirmation",
      "title": "Kind",
      "type": "string"
    },
    "payload": {
      "$ref": "#/$defs/ConfirmationPayload"
    },
    "state": {
      "anyOf": [
        {
          "enum": [
            "confirmed",
            "cancelled",
            "expired"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "State"
    },
    "summary": {
      "title": "Summary",
      "type": "string"
    }
  },
  "required": [
    "summary",
    "payload"
  ],
  "title": "ConfirmationBlock",
  "type": "object"
}
```

### FormBlock

```json
{
  "$defs": {
    "FormPayload": {
      "additionalProperties": false,
      "description": "`type` opens at `text` and `number` alone \u2014 the two a renderer draws.\nThe wider set a form could plausibly want (a choice, a toggle, a date, a\ntime) is deliberately absent rather than declared and unhandled: a type\nnothing can draw is a promise the system cannot keep, and each of those\nneeds a design before it needs a schema.\n\n`callback_id` sits here because the hub stamps it onto every action update,\nwhich is what lets an agent resolve the right pending decision without\nkeeping a `message_id`\u2192handle map it would lose on each deploy.\n\nA form declares no actions. It has exactly one, so naming it would be a\nsecond name for the same thing, and its tap carries the reserved\n`\"submit\"` \u2014 the same shape `confirmation` uses for its own two fixed\noutcomes. `submit_label` is the words on that one control, nothing more.",
      "properties": {
        "callback_id": {
          "title": "Callback Id",
          "type": "string"
        },
        "rows": {
          "items": {
            "$ref": "#/$defs/FormRow"
          },
          "title": "Rows",
          "type": "array"
        },
        "submit_label": {
          "default": "Submit",
          "title": "Submit Label",
          "type": "string"
        },
        "subtitle": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subtitle"
        },
        "title": {
          "title": "Title",
          "type": "string"
        }
      },
      "required": [
        "callback_id",
        "title",
        "rows"
      ],
      "title": "FormPayload",
      "type": "object"
    },
    "FormRow": {
      "additionalProperties": false,
      "description": "One labelled thing being filled in, and the one or two boxes it takes \u2014\n*\"Bench press\"* with a reps box and a kg box beside it. The grouping lives\nhere rather than being inferred from a flat list because the resolved and\nexpired renders collapse a **row** to a single value, so it has to exist in\nthe payload rather than be re-derived at draw time. A flat list carrying a\n`group` key was rejected for the reason the union above exists: the\ngrouping would become a convention the hub cannot check.",
      "properties": {
        "fields": {
          "items": {
            "discriminator": {
              "mapping": {
                "number": "#/$defs/NumberField",
                "text": "#/$defs/TextField"
              },
              "propertyName": "type"
            },
            "oneOf": [
              {
                "$ref": "#/$defs/TextField"
              },
              {
                "$ref": "#/$defs/NumberField"
              }
            ]
          },
          "minItems": 1,
          "title": "Fields",
          "type": "array"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "label",
        "fields"
      ],
      "title": "FormRow",
      "type": "object"
    },
    "NumberField": {
      "additionalProperties": false,
      "description": "A numeric box. Identical to `TextField` but for what `default` may hold,\nwhich is the whole reason these are two models rather than one with a\n`type` tag beside an untyped default.",
      "properties": {
        "default": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Default"
        },
        "field_id": {
          "title": "Field Id",
          "type": "string"
        },
        "type": {
          "const": "number",
          "default": "number",
          "title": "Type",
          "type": "string"
        },
        "unit": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Unit"
        }
      },
      "required": [
        "field_id"
      ],
      "title": "NumberField",
      "type": "object"
    },
    "TextField": {
      "additionalProperties": false,
      "description": "A free-text box. `default` is the *prepopulation*, not a hint: a form\narrives with the agent's best guess already in the box, to be corrected or\naccepted rather than composed from nothing. There is no placeholder \u2014 that\nrenders only into an empty box, which is a state nothing draws \u2014 and no\nper-field label, because `unit` is what names a box.",
      "properties": {
        "default": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Default"
        },
        "field_id": {
          "title": "Field Id",
          "type": "string"
        },
        "type": {
          "const": "text",
          "default": "text",
          "title": "Type",
          "type": "string"
        },
        "unit": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Unit"
        }
      },
      "required": [
        "field_id"
      ],
      "title": "TextField",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "description": "`state` narrows to two values, not three: a form has nothing to decline,\nso there is no `cancelled` to sit beside `logged`. `expired` still applies \u2014\nthat is the absence of a decision, which a form can have like any other\nkind.\n\n**`values` is the one field in this contract a *client* writes onto a row\nthe agent authored**, and it is what makes a resolved form a record of what\nwas submitted rather than an echo of what was proposed. Everything else\nhere came from the agent's send; `state` is agent-written too, through\n`PATCH`, even though a tap is what triggers it. This arrives on\n`POST /v1/actions`, is validated against the fields `payload` declares, and\nis stamped by the hub in that same transaction.\n\nIt sits here rather than inside `FormPayload` because the payload is what\nthe agent proposed, and merging the user's own work into it would erase the\none distinction the field exists to make.\n\n**The send route refuses it, and that refusal is load-bearing rather than\ntidy.** `values` is *evidence of what the user submitted*, and evidence an\nagent can write is not evidence \u2014 without the refusal an agent could send a\nform pre-stamped with a submission that never happened, and a client would\nrender it under the word `logged`. Declared optional here because a form is\nsent without it and carries it only after a tap; refused at the door\nrather than by this type, since Pydantic cannot see which direction a\nblock is travelling.",
  "properties": {
    "kind": {
      "const": "form",
      "default": "form",
      "title": "Kind",
      "type": "string"
    },
    "payload": {
      "$ref": "#/$defs/FormPayload"
    },
    "state": {
      "anyOf": [
        {
          "enum": [
            "logged",
            "expired"
          ],
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "State"
    },
    "summary": {
      "title": "Summary",
      "type": "string"
    },
    "values": {
      "anyOf": [
        {
          "additionalProperties": {
            "anyOf": [
              {
                "type": "string"
              },
              {
                "type": "integer"
              },
              {
                "type": "number"
              },
              {
                "type": "null"
              }
            ]
          },
          "type": "object"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Values"
    }
  },
  "required": [
    "summary",
    "payload"
  ],
  "title": "FormBlock",
  "type": "object"
}
```

### HealthResponse

```json
{
  "properties": {
    "contract_version": {
      "title": "Contract Version",
      "type": "string"
    },
    "history_epoch": {
      "title": "History Epoch",
      "type": "string"
    },
    "service": {
      "const": "jarvis-app-hub",
      "title": "Service",
      "type": "string"
    }
  },
  "required": [
    "service",
    "contract_version",
    "history_epoch"
  ],
  "title": "HealthResponse",
  "type": "object"
}
```

### LoginRequest

```json
{
  "additionalProperties": false,
  "properties": {
    "device_name": {
      "title": "Device Name",
      "type": "string"
    },
    "password": {
      "title": "Password",
      "type": "string"
    },
    "username": {
      "title": "Username",
      "type": "string"
    }
  },
  "required": [
    "username",
    "password",
    "device_name"
  ],
  "title": "LoginRequest",
  "type": "object"
}
```

### LoginResponse

```json
{
  "$defs": {
    "UserInfo": {
      "properties": {
        "display_name": {
          "title": "Display Name",
          "type": "string"
        },
        "user_id": {
          "title": "User Id",
          "type": "string"
        }
      },
      "required": [
        "user_id",
        "display_name"
      ],
      "title": "UserInfo",
      "type": "object"
    }
  },
  "properties": {
    "device_id": {
      "pattern": "^d_[0-9A-HJKMNP-TV-Z]{26}$",
      "title": "Device Id",
      "type": "string"
    },
    "device_token": {
      "title": "Device Token",
      "type": "string"
    },
    "user": {
      "$ref": "#/$defs/UserInfo"
    }
  },
  "required": [
    "device_id",
    "device_token",
    "user"
  ],
  "title": "LoginResponse",
  "type": "object"
}
```

### Message

```json
{
  "$defs": {
    "Action": {
      "additionalProperties": false,
      "description": "A tappable option: `card.actions` and `buttons.options` both use this\nshape, so a card's own buttons and a bare `buttons` block behave the same\nway once tapped.",
      "properties": {
        "action_id": {
          "title": "Action Id",
          "type": "string"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "action_id",
        "label"
      ],
      "title": "Action",
      "type": "object"
    },
    "Attachment": {
      "additionalProperties": false,
      "description": "One uploaded blob, as it rides `attachments[]` on a `Message` or the\nresponse of `POST /v1/attachments`.",
      "properties": {
        "blur_preview": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Blur Preview"
        },
        "duration_ms": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Duration Ms"
        },
        "filename": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Filename"
        },
        "height": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Height"
        },
        "id": {
          "pattern": "^att_[0-9A-HJKMNP-TV-Z]{26}$",
          "title": "Id",
          "type": "string"
        },
        "kind": {
          "enum": [
            "image",
            "audio",
            "file"
          ],
          "title": "Kind",
          "type": "string"
        },
        "mime_type": {
          "title": "Mime Type",
          "type": "string"
        },
        "size": {
          "title": "Size",
          "type": "integer"
        },
        "width": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Width"
        }
      },
      "required": [
        "id",
        "kind",
        "mime_type",
        "size"
      ],
      "title": "Attachment",
      "type": "object"
    },
    "ButtonsBlock": {
      "additionalProperties": false,
      "description": "`state` is the selected `action_id` once resolved \u2014 an open string\nrather than an enum, because the values are whatever the author of this\nblock's `options` chose.",
      "properties": {
        "kind": {
          "const": "buttons",
          "default": "buttons",
          "title": "Kind",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/ButtonsPayload"
        },
        "state": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "summary": {
          "title": "Summary",
          "type": "string"
        }
      },
      "required": [
        "summary",
        "payload"
      ],
      "title": "ButtonsBlock",
      "type": "object"
    },
    "ButtonsPayload": {
      "additionalProperties": false,
      "description": "No prose field exists here \u2014 see the module docstring. `options`\nabsorbs what a separate `choice` kind would otherwise do.",
      "properties": {
        "options": {
          "items": {
            "$ref": "#/$defs/Action"
          },
          "title": "Options",
          "type": "array"
        }
      },
      "required": [
        "options"
      ],
      "title": "ButtonsPayload",
      "type": "object"
    },
    "CardBlock": {
      "additionalProperties": false,
      "properties": {
        "kind": {
          "const": "card",
          "default": "card",
          "title": "Kind",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/CardPayload"
        },
        "state": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "summary": {
          "title": "Summary",
          "type": "string"
        }
      },
      "required": [
        "summary",
        "payload"
      ],
      "title": "CardBlock",
      "type": "object"
    },
    "CardPayload": {
      "additionalProperties": false,
      "properties": {
        "actions": {
          "items": {
            "$ref": "#/$defs/Action"
          },
          "title": "Actions",
          "type": "array"
        },
        "body": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Body"
        },
        "subtitle": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subtitle"
        },
        "title": {
          "title": "Title",
          "type": "string"
        }
      },
      "required": [
        "title"
      ],
      "title": "CardPayload",
      "type": "object"
    },
    "ConfirmationBlock": {
      "additionalProperties": false,
      "properties": {
        "kind": {
          "const": "confirmation",
          "default": "confirmation",
          "title": "Kind",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/ConfirmationPayload"
        },
        "state": {
          "anyOf": [
            {
              "enum": [
                "confirmed",
                "cancelled",
                "expired"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "summary": {
          "title": "Summary",
          "type": "string"
        }
      },
      "required": [
        "summary",
        "payload"
      ],
      "title": "ConfirmationBlock",
      "type": "object"
    },
    "ConfirmationPayload": {
      "additionalProperties": false,
      "description": "`body` carries the question this block asks \u2014 required, since nothing\nelse on the block carries it. `title` is optional and renders no row at\nall when absent; it exists for the rarer confirmation whose question\nneeds a heading above detail. The two labels are what vary the button\nwording between one confirmation and another.",
      "properties": {
        "body": {
          "title": "Body",
          "type": "string"
        },
        "callback_id": {
          "title": "Callback Id",
          "type": "string"
        },
        "cancel_label": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Cancel Label"
        },
        "confirm_label": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Confirm Label"
        },
        "title": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Title"
        }
      },
      "required": [
        "callback_id",
        "body"
      ],
      "title": "ConfirmationPayload",
      "type": "object"
    },
    "FormBlock": {
      "additionalProperties": false,
      "description": "`state` narrows to two values, not three: a form has nothing to decline,\nso there is no `cancelled` to sit beside `logged`. `expired` still applies \u2014\nthat is the absence of a decision, which a form can have like any other\nkind.\n\n**`values` is the one field in this contract a *client* writes onto a row\nthe agent authored**, and it is what makes a resolved form a record of what\nwas submitted rather than an echo of what was proposed. Everything else\nhere came from the agent's send; `state` is agent-written too, through\n`PATCH`, even though a tap is what triggers it. This arrives on\n`POST /v1/actions`, is validated against the fields `payload` declares, and\nis stamped by the hub in that same transaction.\n\nIt sits here rather than inside `FormPayload` because the payload is what\nthe agent proposed, and merging the user's own work into it would erase the\none distinction the field exists to make.\n\n**The send route refuses it, and that refusal is load-bearing rather than\ntidy.** `values` is *evidence of what the user submitted*, and evidence an\nagent can write is not evidence \u2014 without the refusal an agent could send a\nform pre-stamped with a submission that never happened, and a client would\nrender it under the word `logged`. Declared optional here because a form is\nsent without it and carries it only after a tap; refused at the door\nrather than by this type, since Pydantic cannot see which direction a\nblock is travelling.",
      "properties": {
        "kind": {
          "const": "form",
          "default": "form",
          "title": "Kind",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/FormPayload"
        },
        "state": {
          "anyOf": [
            {
              "enum": [
                "logged",
                "expired"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "summary": {
          "title": "Summary",
          "type": "string"
        },
        "values": {
          "anyOf": [
            {
              "additionalProperties": {
                "anyOf": [
                  {
                    "type": "string"
                  },
                  {
                    "type": "integer"
                  },
                  {
                    "type": "number"
                  },
                  {
                    "type": "null"
                  }
                ]
              },
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Values"
        }
      },
      "required": [
        "summary",
        "payload"
      ],
      "title": "FormBlock",
      "type": "object"
    },
    "FormPayload": {
      "additionalProperties": false,
      "description": "`type` opens at `text` and `number` alone \u2014 the two a renderer draws.\nThe wider set a form could plausibly want (a choice, a toggle, a date, a\ntime) is deliberately absent rather than declared and unhandled: a type\nnothing can draw is a promise the system cannot keep, and each of those\nneeds a design before it needs a schema.\n\n`callback_id` sits here because the hub stamps it onto every action update,\nwhich is what lets an agent resolve the right pending decision without\nkeeping a `message_id`\u2192handle map it would lose on each deploy.\n\nA form declares no actions. It has exactly one, so naming it would be a\nsecond name for the same thing, and its tap carries the reserved\n`\"submit\"` \u2014 the same shape `confirmation` uses for its own two fixed\noutcomes. `submit_label` is the words on that one control, nothing more.",
      "properties": {
        "callback_id": {
          "title": "Callback Id",
          "type": "string"
        },
        "rows": {
          "items": {
            "$ref": "#/$defs/FormRow"
          },
          "title": "Rows",
          "type": "array"
        },
        "submit_label": {
          "default": "Submit",
          "title": "Submit Label",
          "type": "string"
        },
        "subtitle": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subtitle"
        },
        "title": {
          "title": "Title",
          "type": "string"
        }
      },
      "required": [
        "callback_id",
        "title",
        "rows"
      ],
      "title": "FormPayload",
      "type": "object"
    },
    "FormRow": {
      "additionalProperties": false,
      "description": "One labelled thing being filled in, and the one or two boxes it takes \u2014\n*\"Bench press\"* with a reps box and a kg box beside it. The grouping lives\nhere rather than being inferred from a flat list because the resolved and\nexpired renders collapse a **row** to a single value, so it has to exist in\nthe payload rather than be re-derived at draw time. A flat list carrying a\n`group` key was rejected for the reason the union above exists: the\ngrouping would become a convention the hub cannot check.",
      "properties": {
        "fields": {
          "items": {
            "discriminator": {
              "mapping": {
                "number": "#/$defs/NumberField",
                "text": "#/$defs/TextField"
              },
              "propertyName": "type"
            },
            "oneOf": [
              {
                "$ref": "#/$defs/TextField"
              },
              {
                "$ref": "#/$defs/NumberField"
              }
            ]
          },
          "minItems": 1,
          "title": "Fields",
          "type": "array"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "label",
        "fields"
      ],
      "title": "FormRow",
      "type": "object"
    },
    "MessageMeta": {
      "additionalProperties": false,
      "description": "`source` is informational only \u2014 the client must not branch on it\n(architecture \u00a74: `heartbeat`/`reminder`/`notifier` are this agent's\nconcepts, not universal ones).",
      "properties": {
        "source": {
          "enum": [
            "user",
            "agent",
            "heartbeat",
            "reminder",
            "notifier"
          ],
          "title": "Source",
          "type": "string"
        }
      },
      "required": [
        "source"
      ],
      "title": "MessageMeta",
      "type": "object"
    },
    "NumberField": {
      "additionalProperties": false,
      "description": "A numeric box. Identical to `TextField` but for what `default` may hold,\nwhich is the whole reason these are two models rather than one with a\n`type` tag beside an untyped default.",
      "properties": {
        "default": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Default"
        },
        "field_id": {
          "title": "Field Id",
          "type": "string"
        },
        "type": {
          "const": "number",
          "default": "number",
          "title": "Type",
          "type": "string"
        },
        "unit": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Unit"
        }
      },
      "required": [
        "field_id"
      ],
      "title": "NumberField",
      "type": "object"
    },
    "TextField": {
      "additionalProperties": false,
      "description": "A free-text box. `default` is the *prepopulation*, not a hint: a form\narrives with the agent's best guess already in the box, to be corrected or\naccepted rather than composed from nothing. There is no placeholder \u2014 that\nrenders only into an empty box, which is a state nothing draws \u2014 and no\nper-field label, because `unit` is what names a box.",
      "properties": {
        "default": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Default"
        },
        "field_id": {
          "title": "Field Id",
          "type": "string"
        },
        "type": {
          "const": "text",
          "default": "text",
          "title": "Type",
          "type": "string"
        },
        "unit": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Unit"
        }
      },
      "required": [
        "field_id"
      ],
      "title": "TextField",
      "type": "object"
    }
  },
  "description": "The persistent unit; `id` is the sync cursor (architecture \u00a75).",
  "properties": {
    "attachments": {
      "items": {
        "$ref": "#/$defs/Attachment"
      },
      "title": "Attachments",
      "type": "array"
    },
    "blocks": {
      "anyOf": [
        {
          "items": {
            "discriminator": {
              "mapping": {
                "buttons": "#/$defs/ButtonsBlock",
                "card": "#/$defs/CardBlock",
                "confirmation": "#/$defs/ConfirmationBlock",
                "form": "#/$defs/FormBlock"
              },
              "propertyName": "kind"
            },
            "oneOf": [
              {
                "$ref": "#/$defs/CardBlock"
              },
              {
                "$ref": "#/$defs/FormBlock"
              },
              {
                "$ref": "#/$defs/ButtonsBlock"
              },
              {
                "$ref": "#/$defs/ConfirmationBlock"
              }
            ]
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "title": "Blocks"
    },
    "client_msg_id": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Client Msg Id"
    },
    "client_ts": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Client Ts"
    },
    "created_at": {
      "title": "Created At",
      "type": "string"
    },
    "delivered_at": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Delivered At"
    },
    "id": {
      "title": "Id",
      "type": "integer"
    },
    "meta": {
      "$ref": "#/$defs/MessageMeta"
    },
    "role": {
      "enum": [
        "user",
        "assistant"
      ],
      "title": "Role",
      "type": "string"
    },
    "text": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "title": "Text"
    },
    "updated_at": {
      "title": "Updated At",
      "type": "string"
    }
  },
  "required": [
    "id",
    "client_msg_id",
    "role",
    "text",
    "blocks",
    "attachments",
    "meta",
    "client_ts",
    "delivered_at",
    "created_at",
    "updated_at"
  ],
  "title": "Message",
  "type": "object"
}
```

### MessageMeta

```json
{
  "additionalProperties": false,
  "description": "`source` is informational only \u2014 the client must not branch on it\n(architecture \u00a74: `heartbeat`/`reminder`/`notifier` are this agent's\nconcepts, not universal ones).",
  "properties": {
    "source": {
      "enum": [
        "user",
        "agent",
        "heartbeat",
        "reminder",
        "notifier"
      ],
      "title": "Source",
      "type": "string"
    }
  },
  "required": [
    "source"
  ],
  "title": "MessageMeta",
  "type": "object"
}
```

### MessageUpdate

```json
{
  "$defs": {
    "Action": {
      "additionalProperties": false,
      "description": "A tappable option: `card.actions` and `buttons.options` both use this\nshape, so a card's own buttons and a bare `buttons` block behave the same\nway once tapped.",
      "properties": {
        "action_id": {
          "title": "Action Id",
          "type": "string"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "action_id",
        "label"
      ],
      "title": "Action",
      "type": "object"
    },
    "Attachment": {
      "additionalProperties": false,
      "description": "One uploaded blob, as it rides `attachments[]` on a `Message` or the\nresponse of `POST /v1/attachments`.",
      "properties": {
        "blur_preview": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Blur Preview"
        },
        "duration_ms": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Duration Ms"
        },
        "filename": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Filename"
        },
        "height": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Height"
        },
        "id": {
          "pattern": "^att_[0-9A-HJKMNP-TV-Z]{26}$",
          "title": "Id",
          "type": "string"
        },
        "kind": {
          "enum": [
            "image",
            "audio",
            "file"
          ],
          "title": "Kind",
          "type": "string"
        },
        "mime_type": {
          "title": "Mime Type",
          "type": "string"
        },
        "size": {
          "title": "Size",
          "type": "integer"
        },
        "width": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Width"
        }
      },
      "required": [
        "id",
        "kind",
        "mime_type",
        "size"
      ],
      "title": "Attachment",
      "type": "object"
    },
    "ButtonsBlock": {
      "additionalProperties": false,
      "description": "`state` is the selected `action_id` once resolved \u2014 an open string\nrather than an enum, because the values are whatever the author of this\nblock's `options` chose.",
      "properties": {
        "kind": {
          "const": "buttons",
          "default": "buttons",
          "title": "Kind",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/ButtonsPayload"
        },
        "state": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "summary": {
          "title": "Summary",
          "type": "string"
        }
      },
      "required": [
        "summary",
        "payload"
      ],
      "title": "ButtonsBlock",
      "type": "object"
    },
    "ButtonsPayload": {
      "additionalProperties": false,
      "description": "No prose field exists here \u2014 see the module docstring. `options`\nabsorbs what a separate `choice` kind would otherwise do.",
      "properties": {
        "options": {
          "items": {
            "$ref": "#/$defs/Action"
          },
          "title": "Options",
          "type": "array"
        }
      },
      "required": [
        "options"
      ],
      "title": "ButtonsPayload",
      "type": "object"
    },
    "CardBlock": {
      "additionalProperties": false,
      "properties": {
        "kind": {
          "const": "card",
          "default": "card",
          "title": "Kind",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/CardPayload"
        },
        "state": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "summary": {
          "title": "Summary",
          "type": "string"
        }
      },
      "required": [
        "summary",
        "payload"
      ],
      "title": "CardBlock",
      "type": "object"
    },
    "CardPayload": {
      "additionalProperties": false,
      "properties": {
        "actions": {
          "items": {
            "$ref": "#/$defs/Action"
          },
          "title": "Actions",
          "type": "array"
        },
        "body": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Body"
        },
        "subtitle": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subtitle"
        },
        "title": {
          "title": "Title",
          "type": "string"
        }
      },
      "required": [
        "title"
      ],
      "title": "CardPayload",
      "type": "object"
    },
    "ConfirmationBlock": {
      "additionalProperties": false,
      "properties": {
        "kind": {
          "const": "confirmation",
          "default": "confirmation",
          "title": "Kind",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/ConfirmationPayload"
        },
        "state": {
          "anyOf": [
            {
              "enum": [
                "confirmed",
                "cancelled",
                "expired"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "summary": {
          "title": "Summary",
          "type": "string"
        }
      },
      "required": [
        "summary",
        "payload"
      ],
      "title": "ConfirmationBlock",
      "type": "object"
    },
    "ConfirmationPayload": {
      "additionalProperties": false,
      "description": "`body` carries the question this block asks \u2014 required, since nothing\nelse on the block carries it. `title` is optional and renders no row at\nall when absent; it exists for the rarer confirmation whose question\nneeds a heading above detail. The two labels are what vary the button\nwording between one confirmation and another.",
      "properties": {
        "body": {
          "title": "Body",
          "type": "string"
        },
        "callback_id": {
          "title": "Callback Id",
          "type": "string"
        },
        "cancel_label": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Cancel Label"
        },
        "confirm_label": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Confirm Label"
        },
        "title": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Title"
        }
      },
      "required": [
        "callback_id",
        "body"
      ],
      "title": "ConfirmationPayload",
      "type": "object"
    },
    "FormBlock": {
      "additionalProperties": false,
      "description": "`state` narrows to two values, not three: a form has nothing to decline,\nso there is no `cancelled` to sit beside `logged`. `expired` still applies \u2014\nthat is the absence of a decision, which a form can have like any other\nkind.\n\n**`values` is the one field in this contract a *client* writes onto a row\nthe agent authored**, and it is what makes a resolved form a record of what\nwas submitted rather than an echo of what was proposed. Everything else\nhere came from the agent's send; `state` is agent-written too, through\n`PATCH`, even though a tap is what triggers it. This arrives on\n`POST /v1/actions`, is validated against the fields `payload` declares, and\nis stamped by the hub in that same transaction.\n\nIt sits here rather than inside `FormPayload` because the payload is what\nthe agent proposed, and merging the user's own work into it would erase the\none distinction the field exists to make.\n\n**The send route refuses it, and that refusal is load-bearing rather than\ntidy.** `values` is *evidence of what the user submitted*, and evidence an\nagent can write is not evidence \u2014 without the refusal an agent could send a\nform pre-stamped with a submission that never happened, and a client would\nrender it under the word `logged`. Declared optional here because a form is\nsent without it and carries it only after a tap; refused at the door\nrather than by this type, since Pydantic cannot see which direction a\nblock is travelling.",
      "properties": {
        "kind": {
          "const": "form",
          "default": "form",
          "title": "Kind",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/FormPayload"
        },
        "state": {
          "anyOf": [
            {
              "enum": [
                "logged",
                "expired"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "summary": {
          "title": "Summary",
          "type": "string"
        },
        "values": {
          "anyOf": [
            {
              "additionalProperties": {
                "anyOf": [
                  {
                    "type": "string"
                  },
                  {
                    "type": "integer"
                  },
                  {
                    "type": "number"
                  },
                  {
                    "type": "null"
                  }
                ]
              },
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Values"
        }
      },
      "required": [
        "summary",
        "payload"
      ],
      "title": "FormBlock",
      "type": "object"
    },
    "FormPayload": {
      "additionalProperties": false,
      "description": "`type` opens at `text` and `number` alone \u2014 the two a renderer draws.\nThe wider set a form could plausibly want (a choice, a toggle, a date, a\ntime) is deliberately absent rather than declared and unhandled: a type\nnothing can draw is a promise the system cannot keep, and each of those\nneeds a design before it needs a schema.\n\n`callback_id` sits here because the hub stamps it onto every action update,\nwhich is what lets an agent resolve the right pending decision without\nkeeping a `message_id`\u2192handle map it would lose on each deploy.\n\nA form declares no actions. It has exactly one, so naming it would be a\nsecond name for the same thing, and its tap carries the reserved\n`\"submit\"` \u2014 the same shape `confirmation` uses for its own two fixed\noutcomes. `submit_label` is the words on that one control, nothing more.",
      "properties": {
        "callback_id": {
          "title": "Callback Id",
          "type": "string"
        },
        "rows": {
          "items": {
            "$ref": "#/$defs/FormRow"
          },
          "title": "Rows",
          "type": "array"
        },
        "submit_label": {
          "default": "Submit",
          "title": "Submit Label",
          "type": "string"
        },
        "subtitle": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subtitle"
        },
        "title": {
          "title": "Title",
          "type": "string"
        }
      },
      "required": [
        "callback_id",
        "title",
        "rows"
      ],
      "title": "FormPayload",
      "type": "object"
    },
    "FormRow": {
      "additionalProperties": false,
      "description": "One labelled thing being filled in, and the one or two boxes it takes \u2014\n*\"Bench press\"* with a reps box and a kg box beside it. The grouping lives\nhere rather than being inferred from a flat list because the resolved and\nexpired renders collapse a **row** to a single value, so it has to exist in\nthe payload rather than be re-derived at draw time. A flat list carrying a\n`group` key was rejected for the reason the union above exists: the\ngrouping would become a convention the hub cannot check.",
      "properties": {
        "fields": {
          "items": {
            "discriminator": {
              "mapping": {
                "number": "#/$defs/NumberField",
                "text": "#/$defs/TextField"
              },
              "propertyName": "type"
            },
            "oneOf": [
              {
                "$ref": "#/$defs/TextField"
              },
              {
                "$ref": "#/$defs/NumberField"
              }
            ]
          },
          "minItems": 1,
          "title": "Fields",
          "type": "array"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "label",
        "fields"
      ],
      "title": "FormRow",
      "type": "object"
    },
    "Message": {
      "description": "The persistent unit; `id` is the sync cursor (architecture \u00a75).",
      "properties": {
        "attachments": {
          "items": {
            "$ref": "#/$defs/Attachment"
          },
          "title": "Attachments",
          "type": "array"
        },
        "blocks": {
          "anyOf": [
            {
              "items": {
                "discriminator": {
                  "mapping": {
                    "buttons": "#/$defs/ButtonsBlock",
                    "card": "#/$defs/CardBlock",
                    "confirmation": "#/$defs/ConfirmationBlock",
                    "form": "#/$defs/FormBlock"
                  },
                  "propertyName": "kind"
                },
                "oneOf": [
                  {
                    "$ref": "#/$defs/CardBlock"
                  },
                  {
                    "$ref": "#/$defs/FormBlock"
                  },
                  {
                    "$ref": "#/$defs/ButtonsBlock"
                  },
                  {
                    "$ref": "#/$defs/ConfirmationBlock"
                  }
                ]
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "title": "Blocks"
        },
        "client_msg_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Client Msg Id"
        },
        "client_ts": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Client Ts"
        },
        "created_at": {
          "title": "Created At",
          "type": "string"
        },
        "delivered_at": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Delivered At"
        },
        "id": {
          "title": "Id",
          "type": "integer"
        },
        "meta": {
          "$ref": "#/$defs/MessageMeta"
        },
        "role": {
          "enum": [
            "user",
            "assistant"
          ],
          "title": "Role",
          "type": "string"
        },
        "text": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Text"
        },
        "updated_at": {
          "title": "Updated At",
          "type": "string"
        }
      },
      "required": [
        "id",
        "client_msg_id",
        "role",
        "text",
        "blocks",
        "attachments",
        "meta",
        "client_ts",
        "delivered_at",
        "created_at",
        "updated_at"
      ],
      "title": "Message",
      "type": "object"
    },
    "MessageMeta": {
      "additionalProperties": false,
      "description": "`source` is informational only \u2014 the client must not branch on it\n(architecture \u00a74: `heartbeat`/`reminder`/`notifier` are this agent's\nconcepts, not universal ones).",
      "properties": {
        "source": {
          "enum": [
            "user",
            "agent",
            "heartbeat",
            "reminder",
            "notifier"
          ],
          "title": "Source",
          "type": "string"
        }
      },
      "required": [
        "source"
      ],
      "title": "MessageMeta",
      "type": "object"
    },
    "NumberField": {
      "additionalProperties": false,
      "description": "A numeric box. Identical to `TextField` but for what `default` may hold,\nwhich is the whole reason these are two models rather than one with a\n`type` tag beside an untyped default.",
      "properties": {
        "default": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Default"
        },
        "field_id": {
          "title": "Field Id",
          "type": "string"
        },
        "type": {
          "const": "number",
          "default": "number",
          "title": "Type",
          "type": "string"
        },
        "unit": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Unit"
        }
      },
      "required": [
        "field_id"
      ],
      "title": "NumberField",
      "type": "object"
    },
    "TextField": {
      "additionalProperties": false,
      "description": "A free-text box. `default` is the *prepopulation*, not a hint: a form\narrives with the agent's best guess already in the box, to be corrected or\naccepted rather than composed from nothing. There is no placeholder \u2014 that\nrenders only into an empty box, which is a state nothing draws \u2014 and no\nper-field label, because `unit` is what names a box.",
      "properties": {
        "default": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Default"
        },
        "field_id": {
          "title": "Field Id",
          "type": "string"
        },
        "type": {
          "const": "text",
          "default": "text",
          "title": "Type",
          "type": "string"
        },
        "unit": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Unit"
        }
      },
      "required": [
        "field_id"
      ],
      "title": "TextField",
      "type": "object"
    }
  },
  "properties": {
    "message": {
      "$ref": "#/$defs/Message"
    },
    "type": {
      "const": "message",
      "title": "Type",
      "type": "string"
    },
    "update_id": {
      "title": "Update Id",
      "type": "integer"
    }
  },
  "required": [
    "update_id",
    "type",
    "message"
  ],
  "title": "MessageUpdate",
  "type": "object"
}
```

### MessagesPage

```json
{
  "$defs": {
    "Action": {
      "additionalProperties": false,
      "description": "A tappable option: `card.actions` and `buttons.options` both use this\nshape, so a card's own buttons and a bare `buttons` block behave the same\nway once tapped.",
      "properties": {
        "action_id": {
          "title": "Action Id",
          "type": "string"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "action_id",
        "label"
      ],
      "title": "Action",
      "type": "object"
    },
    "Attachment": {
      "additionalProperties": false,
      "description": "One uploaded blob, as it rides `attachments[]` on a `Message` or the\nresponse of `POST /v1/attachments`.",
      "properties": {
        "blur_preview": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Blur Preview"
        },
        "duration_ms": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Duration Ms"
        },
        "filename": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Filename"
        },
        "height": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Height"
        },
        "id": {
          "pattern": "^att_[0-9A-HJKMNP-TV-Z]{26}$",
          "title": "Id",
          "type": "string"
        },
        "kind": {
          "enum": [
            "image",
            "audio",
            "file"
          ],
          "title": "Kind",
          "type": "string"
        },
        "mime_type": {
          "title": "Mime Type",
          "type": "string"
        },
        "size": {
          "title": "Size",
          "type": "integer"
        },
        "width": {
          "anyOf": [
            {
              "type": "integer"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Width"
        }
      },
      "required": [
        "id",
        "kind",
        "mime_type",
        "size"
      ],
      "title": "Attachment",
      "type": "object"
    },
    "ButtonsBlock": {
      "additionalProperties": false,
      "description": "`state` is the selected `action_id` once resolved \u2014 an open string\nrather than an enum, because the values are whatever the author of this\nblock's `options` chose.",
      "properties": {
        "kind": {
          "const": "buttons",
          "default": "buttons",
          "title": "Kind",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/ButtonsPayload"
        },
        "state": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "summary": {
          "title": "Summary",
          "type": "string"
        }
      },
      "required": [
        "summary",
        "payload"
      ],
      "title": "ButtonsBlock",
      "type": "object"
    },
    "ButtonsPayload": {
      "additionalProperties": false,
      "description": "No prose field exists here \u2014 see the module docstring. `options`\nabsorbs what a separate `choice` kind would otherwise do.",
      "properties": {
        "options": {
          "items": {
            "$ref": "#/$defs/Action"
          },
          "title": "Options",
          "type": "array"
        }
      },
      "required": [
        "options"
      ],
      "title": "ButtonsPayload",
      "type": "object"
    },
    "CardBlock": {
      "additionalProperties": false,
      "properties": {
        "kind": {
          "const": "card",
          "default": "card",
          "title": "Kind",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/CardPayload"
        },
        "state": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "summary": {
          "title": "Summary",
          "type": "string"
        }
      },
      "required": [
        "summary",
        "payload"
      ],
      "title": "CardBlock",
      "type": "object"
    },
    "CardPayload": {
      "additionalProperties": false,
      "properties": {
        "actions": {
          "items": {
            "$ref": "#/$defs/Action"
          },
          "title": "Actions",
          "type": "array"
        },
        "body": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Body"
        },
        "subtitle": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subtitle"
        },
        "title": {
          "title": "Title",
          "type": "string"
        }
      },
      "required": [
        "title"
      ],
      "title": "CardPayload",
      "type": "object"
    },
    "ConfirmationBlock": {
      "additionalProperties": false,
      "properties": {
        "kind": {
          "const": "confirmation",
          "default": "confirmation",
          "title": "Kind",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/ConfirmationPayload"
        },
        "state": {
          "anyOf": [
            {
              "enum": [
                "confirmed",
                "cancelled",
                "expired"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "summary": {
          "title": "Summary",
          "type": "string"
        }
      },
      "required": [
        "summary",
        "payload"
      ],
      "title": "ConfirmationBlock",
      "type": "object"
    },
    "ConfirmationPayload": {
      "additionalProperties": false,
      "description": "`body` carries the question this block asks \u2014 required, since nothing\nelse on the block carries it. `title` is optional and renders no row at\nall when absent; it exists for the rarer confirmation whose question\nneeds a heading above detail. The two labels are what vary the button\nwording between one confirmation and another.",
      "properties": {
        "body": {
          "title": "Body",
          "type": "string"
        },
        "callback_id": {
          "title": "Callback Id",
          "type": "string"
        },
        "cancel_label": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Cancel Label"
        },
        "confirm_label": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Confirm Label"
        },
        "title": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Title"
        }
      },
      "required": [
        "callback_id",
        "body"
      ],
      "title": "ConfirmationPayload",
      "type": "object"
    },
    "FormBlock": {
      "additionalProperties": false,
      "description": "`state` narrows to two values, not three: a form has nothing to decline,\nso there is no `cancelled` to sit beside `logged`. `expired` still applies \u2014\nthat is the absence of a decision, which a form can have like any other\nkind.\n\n**`values` is the one field in this contract a *client* writes onto a row\nthe agent authored**, and it is what makes a resolved form a record of what\nwas submitted rather than an echo of what was proposed. Everything else\nhere came from the agent's send; `state` is agent-written too, through\n`PATCH`, even though a tap is what triggers it. This arrives on\n`POST /v1/actions`, is validated against the fields `payload` declares, and\nis stamped by the hub in that same transaction.\n\nIt sits here rather than inside `FormPayload` because the payload is what\nthe agent proposed, and merging the user's own work into it would erase the\none distinction the field exists to make.\n\n**The send route refuses it, and that refusal is load-bearing rather than\ntidy.** `values` is *evidence of what the user submitted*, and evidence an\nagent can write is not evidence \u2014 without the refusal an agent could send a\nform pre-stamped with a submission that never happened, and a client would\nrender it under the word `logged`. Declared optional here because a form is\nsent without it and carries it only after a tap; refused at the door\nrather than by this type, since Pydantic cannot see which direction a\nblock is travelling.",
      "properties": {
        "kind": {
          "const": "form",
          "default": "form",
          "title": "Kind",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/FormPayload"
        },
        "state": {
          "anyOf": [
            {
              "enum": [
                "logged",
                "expired"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "summary": {
          "title": "Summary",
          "type": "string"
        },
        "values": {
          "anyOf": [
            {
              "additionalProperties": {
                "anyOf": [
                  {
                    "type": "string"
                  },
                  {
                    "type": "integer"
                  },
                  {
                    "type": "number"
                  },
                  {
                    "type": "null"
                  }
                ]
              },
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Values"
        }
      },
      "required": [
        "summary",
        "payload"
      ],
      "title": "FormBlock",
      "type": "object"
    },
    "FormPayload": {
      "additionalProperties": false,
      "description": "`type` opens at `text` and `number` alone \u2014 the two a renderer draws.\nThe wider set a form could plausibly want (a choice, a toggle, a date, a\ntime) is deliberately absent rather than declared and unhandled: a type\nnothing can draw is a promise the system cannot keep, and each of those\nneeds a design before it needs a schema.\n\n`callback_id` sits here because the hub stamps it onto every action update,\nwhich is what lets an agent resolve the right pending decision without\nkeeping a `message_id`\u2192handle map it would lose on each deploy.\n\nA form declares no actions. It has exactly one, so naming it would be a\nsecond name for the same thing, and its tap carries the reserved\n`\"submit\"` \u2014 the same shape `confirmation` uses for its own two fixed\noutcomes. `submit_label` is the words on that one control, nothing more.",
      "properties": {
        "callback_id": {
          "title": "Callback Id",
          "type": "string"
        },
        "rows": {
          "items": {
            "$ref": "#/$defs/FormRow"
          },
          "title": "Rows",
          "type": "array"
        },
        "submit_label": {
          "default": "Submit",
          "title": "Submit Label",
          "type": "string"
        },
        "subtitle": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subtitle"
        },
        "title": {
          "title": "Title",
          "type": "string"
        }
      },
      "required": [
        "callback_id",
        "title",
        "rows"
      ],
      "title": "FormPayload",
      "type": "object"
    },
    "FormRow": {
      "additionalProperties": false,
      "description": "One labelled thing being filled in, and the one or two boxes it takes \u2014\n*\"Bench press\"* with a reps box and a kg box beside it. The grouping lives\nhere rather than being inferred from a flat list because the resolved and\nexpired renders collapse a **row** to a single value, so it has to exist in\nthe payload rather than be re-derived at draw time. A flat list carrying a\n`group` key was rejected for the reason the union above exists: the\ngrouping would become a convention the hub cannot check.",
      "properties": {
        "fields": {
          "items": {
            "discriminator": {
              "mapping": {
                "number": "#/$defs/NumberField",
                "text": "#/$defs/TextField"
              },
              "propertyName": "type"
            },
            "oneOf": [
              {
                "$ref": "#/$defs/TextField"
              },
              {
                "$ref": "#/$defs/NumberField"
              }
            ]
          },
          "minItems": 1,
          "title": "Fields",
          "type": "array"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "label",
        "fields"
      ],
      "title": "FormRow",
      "type": "object"
    },
    "Message": {
      "description": "The persistent unit; `id` is the sync cursor (architecture \u00a75).",
      "properties": {
        "attachments": {
          "items": {
            "$ref": "#/$defs/Attachment"
          },
          "title": "Attachments",
          "type": "array"
        },
        "blocks": {
          "anyOf": [
            {
              "items": {
                "discriminator": {
                  "mapping": {
                    "buttons": "#/$defs/ButtonsBlock",
                    "card": "#/$defs/CardBlock",
                    "confirmation": "#/$defs/ConfirmationBlock",
                    "form": "#/$defs/FormBlock"
                  },
                  "propertyName": "kind"
                },
                "oneOf": [
                  {
                    "$ref": "#/$defs/CardBlock"
                  },
                  {
                    "$ref": "#/$defs/FormBlock"
                  },
                  {
                    "$ref": "#/$defs/ButtonsBlock"
                  },
                  {
                    "$ref": "#/$defs/ConfirmationBlock"
                  }
                ]
              },
              "type": "array"
            },
            {
              "type": "null"
            }
          ],
          "title": "Blocks"
        },
        "client_msg_id": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Client Msg Id"
        },
        "client_ts": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Client Ts"
        },
        "created_at": {
          "title": "Created At",
          "type": "string"
        },
        "delivered_at": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Delivered At"
        },
        "id": {
          "title": "Id",
          "type": "integer"
        },
        "meta": {
          "$ref": "#/$defs/MessageMeta"
        },
        "role": {
          "enum": [
            "user",
            "assistant"
          ],
          "title": "Role",
          "type": "string"
        },
        "text": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "title": "Text"
        },
        "updated_at": {
          "title": "Updated At",
          "type": "string"
        }
      },
      "required": [
        "id",
        "client_msg_id",
        "role",
        "text",
        "blocks",
        "attachments",
        "meta",
        "client_ts",
        "delivered_at",
        "created_at",
        "updated_at"
      ],
      "title": "Message",
      "type": "object"
    },
    "MessageMeta": {
      "additionalProperties": false,
      "description": "`source` is informational only \u2014 the client must not branch on it\n(architecture \u00a74: `heartbeat`/`reminder`/`notifier` are this agent's\nconcepts, not universal ones).",
      "properties": {
        "source": {
          "enum": [
            "user",
            "agent",
            "heartbeat",
            "reminder",
            "notifier"
          ],
          "title": "Source",
          "type": "string"
        }
      },
      "required": [
        "source"
      ],
      "title": "MessageMeta",
      "type": "object"
    },
    "NumberField": {
      "additionalProperties": false,
      "description": "A numeric box. Identical to `TextField` but for what `default` may hold,\nwhich is the whole reason these are two models rather than one with a\n`type` tag beside an untyped default.",
      "properties": {
        "default": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Default"
        },
        "field_id": {
          "title": "Field Id",
          "type": "string"
        },
        "type": {
          "const": "number",
          "default": "number",
          "title": "Type",
          "type": "string"
        },
        "unit": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Unit"
        }
      },
      "required": [
        "field_id"
      ],
      "title": "NumberField",
      "type": "object"
    },
    "TextField": {
      "additionalProperties": false,
      "description": "A free-text box. `default` is the *prepopulation*, not a hint: a form\narrives with the agent's best guess already in the box, to be corrected or\naccepted rather than composed from nothing. There is no placeholder \u2014 that\nrenders only into an empty box, which is a state nothing draws \u2014 and no\nper-field label, because `unit` is what names a box.",
      "properties": {
        "default": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Default"
        },
        "field_id": {
          "title": "Field Id",
          "type": "string"
        },
        "type": {
          "const": "text",
          "default": "text",
          "title": "Type",
          "type": "string"
        },
        "unit": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Unit"
        }
      },
      "required": [
        "field_id"
      ],
      "title": "TextField",
      "type": "object"
    }
  },
  "description": "`GET /v1/messages`'s response shape. Cursors are plain integers \u2014 the\nmessage `id` itself \u2014 so there is no separate anchor id to carry\nalongside the page; a caller pages on from `items[0].id` /\n`items[-1].id` directly.",
  "properties": {
    "has_more": {
      "title": "Has More",
      "type": "boolean"
    },
    "items": {
      "items": {
        "$ref": "#/$defs/Message"
      },
      "title": "Items",
      "type": "array"
    }
  },
  "required": [
    "items",
    "has_more"
  ],
  "title": "MessagesPage",
  "type": "object"
}
```

### PushRegisterRequest

```json
{
  "additionalProperties": false,
  "description": "`POST /v1/push/register`'s body. The device is never named on the\nwire \u2014 it comes from the caller's own bearer token, so a device may only\nregister itself.",
  "properties": {
    "platform": {
      "const": "android",
      "title": "Platform",
      "type": "string"
    },
    "token_or_topic": {
      "title": "Token Or Topic",
      "type": "string"
    }
  },
  "required": [
    "platform",
    "token_or_topic"
  ],
  "title": "PushRegisterRequest",
  "type": "object"
}
```

### SendMessageRequest

```json
{
  "$defs": {
    "Action": {
      "additionalProperties": false,
      "description": "A tappable option: `card.actions` and `buttons.options` both use this\nshape, so a card's own buttons and a bare `buttons` block behave the same\nway once tapped.",
      "properties": {
        "action_id": {
          "title": "Action Id",
          "type": "string"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "action_id",
        "label"
      ],
      "title": "Action",
      "type": "object"
    },
    "ButtonsBlock": {
      "additionalProperties": false,
      "description": "`state` is the selected `action_id` once resolved \u2014 an open string\nrather than an enum, because the values are whatever the author of this\nblock's `options` chose.",
      "properties": {
        "kind": {
          "const": "buttons",
          "default": "buttons",
          "title": "Kind",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/ButtonsPayload"
        },
        "state": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "summary": {
          "title": "Summary",
          "type": "string"
        }
      },
      "required": [
        "summary",
        "payload"
      ],
      "title": "ButtonsBlock",
      "type": "object"
    },
    "ButtonsPayload": {
      "additionalProperties": false,
      "description": "No prose field exists here \u2014 see the module docstring. `options`\nabsorbs what a separate `choice` kind would otherwise do.",
      "properties": {
        "options": {
          "items": {
            "$ref": "#/$defs/Action"
          },
          "title": "Options",
          "type": "array"
        }
      },
      "required": [
        "options"
      ],
      "title": "ButtonsPayload",
      "type": "object"
    },
    "CardBlock": {
      "additionalProperties": false,
      "properties": {
        "kind": {
          "const": "card",
          "default": "card",
          "title": "Kind",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/CardPayload"
        },
        "state": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "summary": {
          "title": "Summary",
          "type": "string"
        }
      },
      "required": [
        "summary",
        "payload"
      ],
      "title": "CardBlock",
      "type": "object"
    },
    "CardPayload": {
      "additionalProperties": false,
      "properties": {
        "actions": {
          "items": {
            "$ref": "#/$defs/Action"
          },
          "title": "Actions",
          "type": "array"
        },
        "body": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Body"
        },
        "subtitle": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subtitle"
        },
        "title": {
          "title": "Title",
          "type": "string"
        }
      },
      "required": [
        "title"
      ],
      "title": "CardPayload",
      "type": "object"
    },
    "ConfirmationBlock": {
      "additionalProperties": false,
      "properties": {
        "kind": {
          "const": "confirmation",
          "default": "confirmation",
          "title": "Kind",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/ConfirmationPayload"
        },
        "state": {
          "anyOf": [
            {
              "enum": [
                "confirmed",
                "cancelled",
                "expired"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "summary": {
          "title": "Summary",
          "type": "string"
        }
      },
      "required": [
        "summary",
        "payload"
      ],
      "title": "ConfirmationBlock",
      "type": "object"
    },
    "ConfirmationPayload": {
      "additionalProperties": false,
      "description": "`body` carries the question this block asks \u2014 required, since nothing\nelse on the block carries it. `title` is optional and renders no row at\nall when absent; it exists for the rarer confirmation whose question\nneeds a heading above detail. The two labels are what vary the button\nwording between one confirmation and another.",
      "properties": {
        "body": {
          "title": "Body",
          "type": "string"
        },
        "callback_id": {
          "title": "Callback Id",
          "type": "string"
        },
        "cancel_label": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Cancel Label"
        },
        "confirm_label": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Confirm Label"
        },
        "title": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Title"
        }
      },
      "required": [
        "callback_id",
        "body"
      ],
      "title": "ConfirmationPayload",
      "type": "object"
    },
    "FormBlock": {
      "additionalProperties": false,
      "description": "`state` narrows to two values, not three: a form has nothing to decline,\nso there is no `cancelled` to sit beside `logged`. `expired` still applies \u2014\nthat is the absence of a decision, which a form can have like any other\nkind.\n\n**`values` is the one field in this contract a *client* writes onto a row\nthe agent authored**, and it is what makes a resolved form a record of what\nwas submitted rather than an echo of what was proposed. Everything else\nhere came from the agent's send; `state` is agent-written too, through\n`PATCH`, even though a tap is what triggers it. This arrives on\n`POST /v1/actions`, is validated against the fields `payload` declares, and\nis stamped by the hub in that same transaction.\n\nIt sits here rather than inside `FormPayload` because the payload is what\nthe agent proposed, and merging the user's own work into it would erase the\none distinction the field exists to make.\n\n**The send route refuses it, and that refusal is load-bearing rather than\ntidy.** `values` is *evidence of what the user submitted*, and evidence an\nagent can write is not evidence \u2014 without the refusal an agent could send a\nform pre-stamped with a submission that never happened, and a client would\nrender it under the word `logged`. Declared optional here because a form is\nsent without it and carries it only after a tap; refused at the door\nrather than by this type, since Pydantic cannot see which direction a\nblock is travelling.",
      "properties": {
        "kind": {
          "const": "form",
          "default": "form",
          "title": "Kind",
          "type": "string"
        },
        "payload": {
          "$ref": "#/$defs/FormPayload"
        },
        "state": {
          "anyOf": [
            {
              "enum": [
                "logged",
                "expired"
              ],
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "State"
        },
        "summary": {
          "title": "Summary",
          "type": "string"
        },
        "values": {
          "anyOf": [
            {
              "additionalProperties": {
                "anyOf": [
                  {
                    "type": "string"
                  },
                  {
                    "type": "integer"
                  },
                  {
                    "type": "number"
                  },
                  {
                    "type": "null"
                  }
                ]
              },
              "type": "object"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Values"
        }
      },
      "required": [
        "summary",
        "payload"
      ],
      "title": "FormBlock",
      "type": "object"
    },
    "FormPayload": {
      "additionalProperties": false,
      "description": "`type` opens at `text` and `number` alone \u2014 the two a renderer draws.\nThe wider set a form could plausibly want (a choice, a toggle, a date, a\ntime) is deliberately absent rather than declared and unhandled: a type\nnothing can draw is a promise the system cannot keep, and each of those\nneeds a design before it needs a schema.\n\n`callback_id` sits here because the hub stamps it onto every action update,\nwhich is what lets an agent resolve the right pending decision without\nkeeping a `message_id`\u2192handle map it would lose on each deploy.\n\nA form declares no actions. It has exactly one, so naming it would be a\nsecond name for the same thing, and its tap carries the reserved\n`\"submit\"` \u2014 the same shape `confirmation` uses for its own two fixed\noutcomes. `submit_label` is the words on that one control, nothing more.",
      "properties": {
        "callback_id": {
          "title": "Callback Id",
          "type": "string"
        },
        "rows": {
          "items": {
            "$ref": "#/$defs/FormRow"
          },
          "title": "Rows",
          "type": "array"
        },
        "submit_label": {
          "default": "Submit",
          "title": "Submit Label",
          "type": "string"
        },
        "subtitle": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Subtitle"
        },
        "title": {
          "title": "Title",
          "type": "string"
        }
      },
      "required": [
        "callback_id",
        "title",
        "rows"
      ],
      "title": "FormPayload",
      "type": "object"
    },
    "FormRow": {
      "additionalProperties": false,
      "description": "One labelled thing being filled in, and the one or two boxes it takes \u2014\n*\"Bench press\"* with a reps box and a kg box beside it. The grouping lives\nhere rather than being inferred from a flat list because the resolved and\nexpired renders collapse a **row** to a single value, so it has to exist in\nthe payload rather than be re-derived at draw time. A flat list carrying a\n`group` key was rejected for the reason the union above exists: the\ngrouping would become a convention the hub cannot check.",
      "properties": {
        "fields": {
          "items": {
            "discriminator": {
              "mapping": {
                "number": "#/$defs/NumberField",
                "text": "#/$defs/TextField"
              },
              "propertyName": "type"
            },
            "oneOf": [
              {
                "$ref": "#/$defs/TextField"
              },
              {
                "$ref": "#/$defs/NumberField"
              }
            ]
          },
          "minItems": 1,
          "title": "Fields",
          "type": "array"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "label",
        "fields"
      ],
      "title": "FormRow",
      "type": "object"
    },
    "NumberField": {
      "additionalProperties": false,
      "description": "A numeric box. Identical to `TextField` but for what `default` may hold,\nwhich is the whole reason these are two models rather than one with a\n`type` tag beside an untyped default.",
      "properties": {
        "default": {
          "anyOf": [
            {
              "type": "number"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Default"
        },
        "field_id": {
          "title": "Field Id",
          "type": "string"
        },
        "type": {
          "const": "number",
          "default": "number",
          "title": "Type",
          "type": "string"
        },
        "unit": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Unit"
        }
      },
      "required": [
        "field_id"
      ],
      "title": "NumberField",
      "type": "object"
    },
    "TextField": {
      "additionalProperties": false,
      "description": "A free-text box. `default` is the *prepopulation*, not a hint: a form\narrives with the agent's best guess already in the box, to be corrected or\naccepted rather than composed from nothing. There is no placeholder \u2014 that\nrenders only into an empty box, which is a state nothing draws \u2014 and no\nper-field label, because `unit` is what names a box.",
      "properties": {
        "default": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Default"
        },
        "field_id": {
          "title": "Field Id",
          "type": "string"
        },
        "type": {
          "const": "text",
          "default": "text",
          "title": "Type",
          "type": "string"
        },
        "unit": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Unit"
        }
      },
      "required": [
        "field_id"
      ],
      "title": "TextField",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "description": "`POST /v1/messages`'s body. `client_msg_id` is the idempotency key \u2014\na replay returns the row it already produced rather than a new one.\n\n`blocks` carries at most one entry \u2014 every kind is interactive\n(architecture \u00a75), and `{message_id, action_id}` is only an unambiguous\naddress for a tap when a message carries at most one block. A second\nblock goes out as a second message.",
  "properties": {
    "attachment_ids": {
      "items": {
        "pattern": "^att_[0-9A-HJKMNP-TV-Z]{26}$",
        "type": "string"
      },
      "title": "Attachment Ids",
      "type": "array"
    },
    "blocks": {
      "anyOf": [
        {
          "items": {
            "discriminator": {
              "mapping": {
                "buttons": "#/$defs/ButtonsBlock",
                "card": "#/$defs/CardBlock",
                "confirmation": "#/$defs/ConfirmationBlock",
                "form": "#/$defs/FormBlock"
              },
              "propertyName": "kind"
            },
            "oneOf": [
              {
                "$ref": "#/$defs/CardBlock"
              },
              {
                "$ref": "#/$defs/FormBlock"
              },
              {
                "$ref": "#/$defs/ButtonsBlock"
              },
              {
                "$ref": "#/$defs/ConfirmationBlock"
              }
            ]
          },
          "type": "array"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Blocks"
    },
    "client_msg_id": {
      "pattern": "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
      "title": "Client Msg Id",
      "type": "string"
    },
    "client_ts": {
      "title": "Client Ts",
      "type": "string"
    },
    "text": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Text"
    }
  },
  "required": [
    "client_msg_id",
    "client_ts"
  ],
  "title": "SendMessageRequest",
  "type": "object"
}
```

### UserInfo

```json
{
  "properties": {
    "display_name": {
      "title": "Display Name",
      "type": "string"
    },
    "user_id": {
      "title": "User Id",
      "type": "string"
    }
  },
  "required": [
    "user_id",
    "display_name"
  ],
  "title": "UserInfo",
  "type": "object"
}
```

