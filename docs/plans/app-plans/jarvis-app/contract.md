<!-- GENERATED FILE. Run `python scripts/export_contract.py` to refresh;
     `--check` verifies it is current (used in CI). Never hand-edited. -->

# jarvis-app wire contract

JSON Schema plus the endpoint list, generated from the Pydantic models and
the mounted routes under `backend/jarvis_app_backend`.

`contract_version`: `f1633277132cbedf`

## Endpoints

- `GET /bot/v1/updates` — Get Updates
- `GET /v1/commands` — Get Commands
- `GET /v1/events` — Events
- `GET /v1/health` — Health
- `GET /v1/messages` — Get Messages
- `PATCH /bot/v1/messages/{message_id}` — Bot Patch
- `POST /bot/v1/commands` — Declare Commands
- `POST /bot/v1/events` — Bot Event
- `POST /bot/v1/messages` — Bot Send
- `POST /v1/auth/login` — Login
- `POST /v1/auth/logout` — Logout
- `POST /v1/messages` — Send Message

## Models

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

### BotEvent

```json
{
  "additionalProperties": false,
  "description": "An ephemeral event the agent relays to the client's stream \u2014 a tool chip\nor a stream delta (architecture \u00a75 SSE catalog). Never persisted and carries\nno cursor, so the stream frames it without an `id:` line.\n\n`type` is strict: an unknown one is a 422, not a mystery event relayed to the\nphone. It admits the whole catalog, not only the two tool-chip kinds a first\nagent sends \u2014 the agent, not the hub, chooses which to emit, and each is a\nworking relay end to end. `data` is opaque: the hub carries the chip's shape,\nit does not interpret it.",
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
        "agent_message_final"
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
      "description": "No prose field exists here \u2014 see the module docstring. `options`\nabsorbs what a separate `choice` kind would otherwise do; `state` is the\nselected `action_id` once resolved, or `None` while the block is still\nlive.",
      "properties": {
        "kind": {
          "const": "buttons",
          "default": "buttons",
          "title": "Kind",
          "type": "string"
        },
        "options": {
          "items": {
            "$ref": "#/$defs/Action"
          },
          "title": "Options",
          "type": "array"
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
        }
      },
      "required": [
        "options"
      ],
      "title": "ButtonsBlock",
      "type": "object"
    },
    "CardBlock": {
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
        "image": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Image"
        },
        "kind": {
          "const": "card",
          "default": "card",
          "title": "Kind",
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
        "title"
      ],
      "title": "CardBlock",
      "type": "object"
    },
    "ConfirmationBlock": {
      "additionalProperties": false,
      "description": "No prose field exists here either \u2014 the question this block asks\nlives in the message's own `text`, never in the block.",
      "properties": {
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
        "kind": {
          "const": "confirmation",
          "default": "confirmation",
          "title": "Kind",
          "type": "string"
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
        }
      },
      "required": [
        "callback_id"
      ],
      "title": "ConfirmationBlock",
      "type": "object"
    },
    "FormBlock": {
      "additionalProperties": false,
      "properties": {
        "fields": {
          "items": {
            "$ref": "#/$defs/FormField"
          },
          "title": "Fields",
          "type": "array"
        },
        "kind": {
          "const": "form",
          "default": "form",
          "title": "Kind",
          "type": "string"
        },
        "submit_label": {
          "default": "Submit",
          "title": "Submit Label",
          "type": "string"
        }
      },
      "required": [
        "fields"
      ],
      "title": "FormBlock",
      "type": "object"
    },
    "FormField": {
      "additionalProperties": false,
      "properties": {
        "field_id": {
          "title": "Field Id",
          "type": "string"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "field_id",
        "label"
      ],
      "title": "FormField",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "description": "Replaces a message's blocks \u2014 how the agent resolves a live one (a\n`confirmation` to `confirmed`/`cancelled`/`expired`, a `buttons` `state`).\nBlocks are validated strict, same as a send.",
  "properties": {
    "blocks": {
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
      "title": "Blocks",
      "type": "array"
    }
  },
  "required": [
    "blocks"
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
      "description": "No prose field exists here \u2014 see the module docstring. `options`\nabsorbs what a separate `choice` kind would otherwise do; `state` is the\nselected `action_id` once resolved, or `None` while the block is still\nlive.",
      "properties": {
        "kind": {
          "const": "buttons",
          "default": "buttons",
          "title": "Kind",
          "type": "string"
        },
        "options": {
          "items": {
            "$ref": "#/$defs/Action"
          },
          "title": "Options",
          "type": "array"
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
        }
      },
      "required": [
        "options"
      ],
      "title": "ButtonsBlock",
      "type": "object"
    },
    "CardBlock": {
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
        "image": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Image"
        },
        "kind": {
          "const": "card",
          "default": "card",
          "title": "Kind",
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
        "title"
      ],
      "title": "CardBlock",
      "type": "object"
    },
    "ConfirmationBlock": {
      "additionalProperties": false,
      "description": "No prose field exists here either \u2014 the question this block asks\nlives in the message's own `text`, never in the block.",
      "properties": {
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
        "kind": {
          "const": "confirmation",
          "default": "confirmation",
          "title": "Kind",
          "type": "string"
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
        }
      },
      "required": [
        "callback_id"
      ],
      "title": "ConfirmationBlock",
      "type": "object"
    },
    "FormBlock": {
      "additionalProperties": false,
      "properties": {
        "fields": {
          "items": {
            "$ref": "#/$defs/FormField"
          },
          "title": "Fields",
          "type": "array"
        },
        "kind": {
          "const": "form",
          "default": "form",
          "title": "Kind",
          "type": "string"
        },
        "submit_label": {
          "default": "Submit",
          "title": "Submit Label",
          "type": "string"
        }
      },
      "required": [
        "fields"
      ],
      "title": "FormBlock",
      "type": "object"
    },
    "FormField": {
      "additionalProperties": false,
      "properties": {
        "field_id": {
          "title": "Field Id",
          "type": "string"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "field_id",
        "label"
      ],
      "title": "FormField",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "properties": {
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

### BotUpdate

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
      "description": "No prose field exists here \u2014 see the module docstring. `options`\nabsorbs what a separate `choice` kind would otherwise do; `state` is the\nselected `action_id` once resolved, or `None` while the block is still\nlive.",
      "properties": {
        "kind": {
          "const": "buttons",
          "default": "buttons",
          "title": "Kind",
          "type": "string"
        },
        "options": {
          "items": {
            "$ref": "#/$defs/Action"
          },
          "title": "Options",
          "type": "array"
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
        }
      },
      "required": [
        "options"
      ],
      "title": "ButtonsBlock",
      "type": "object"
    },
    "CardBlock": {
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
        "image": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Image"
        },
        "kind": {
          "const": "card",
          "default": "card",
          "title": "Kind",
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
        "title"
      ],
      "title": "CardBlock",
      "type": "object"
    },
    "ConfirmationBlock": {
      "additionalProperties": false,
      "description": "No prose field exists here either \u2014 the question this block asks\nlives in the message's own `text`, never in the block.",
      "properties": {
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
        "kind": {
          "const": "confirmation",
          "default": "confirmation",
          "title": "Kind",
          "type": "string"
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
        }
      },
      "required": [
        "callback_id"
      ],
      "title": "ConfirmationBlock",
      "type": "object"
    },
    "FormBlock": {
      "additionalProperties": false,
      "properties": {
        "fields": {
          "items": {
            "$ref": "#/$defs/FormField"
          },
          "title": "Fields",
          "type": "array"
        },
        "kind": {
          "const": "form",
          "default": "form",
          "title": "Kind",
          "type": "string"
        },
        "submit_label": {
          "default": "Submit",
          "title": "Submit Label",
          "type": "string"
        }
      },
      "required": [
        "fields"
      ],
      "title": "FormBlock",
      "type": "object"
    },
    "FormField": {
      "additionalProperties": false,
      "properties": {
        "field_id": {
          "title": "Field Id",
          "type": "string"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "field_id",
        "label"
      ],
      "title": "FormField",
      "type": "object"
    },
    "Message": {
      "description": "The persistent unit; `id` is the sync cursor (architecture \u00a75).",
      "properties": {
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
  "title": "BotUpdate",
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
    }
  },
  "additionalProperties": false,
  "description": "No prose field exists here \u2014 see the module docstring. `options`\nabsorbs what a separate `choice` kind would otherwise do; `state` is the\nselected `action_id` once resolved, or `None` while the block is still\nlive.",
  "properties": {
    "kind": {
      "const": "buttons",
      "default": "buttons",
      "title": "Kind",
      "type": "string"
    },
    "options": {
      "items": {
        "$ref": "#/$defs/Action"
      },
      "title": "Options",
      "type": "array"
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
    }
  },
  "required": [
    "options"
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
    }
  },
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
    "image": {
      "anyOf": [
        {
          "type": "string"
        },
        {
          "type": "null"
        }
      ],
      "default": null,
      "title": "Image"
    },
    "kind": {
      "const": "card",
      "default": "card",
      "title": "Kind",
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
    "title"
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
  "additionalProperties": false,
  "description": "No prose field exists here either \u2014 the question this block asks\nlives in the message's own `text`, never in the block.",
  "properties": {
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
    "kind": {
      "const": "confirmation",
      "default": "confirmation",
      "title": "Kind",
      "type": "string"
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
    }
  },
  "required": [
    "callback_id"
  ],
  "title": "ConfirmationBlock",
  "type": "object"
}
```

### FormBlock

```json
{
  "$defs": {
    "FormField": {
      "additionalProperties": false,
      "properties": {
        "field_id": {
          "title": "Field Id",
          "type": "string"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "field_id",
        "label"
      ],
      "title": "FormField",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "properties": {
    "fields": {
      "items": {
        "$ref": "#/$defs/FormField"
      },
      "title": "Fields",
      "type": "array"
    },
    "kind": {
      "const": "form",
      "default": "form",
      "title": "Kind",
      "type": "string"
    },
    "submit_label": {
      "default": "Submit",
      "title": "Submit Label",
      "type": "string"
    }
  },
  "required": [
    "fields"
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
    "service": {
      "const": "jarvis-app-hub",
      "title": "Service",
      "type": "string"
    }
  },
  "required": [
    "service",
    "contract_version"
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
    "ButtonsBlock": {
      "additionalProperties": false,
      "description": "No prose field exists here \u2014 see the module docstring. `options`\nabsorbs what a separate `choice` kind would otherwise do; `state` is the\nselected `action_id` once resolved, or `None` while the block is still\nlive.",
      "properties": {
        "kind": {
          "const": "buttons",
          "default": "buttons",
          "title": "Kind",
          "type": "string"
        },
        "options": {
          "items": {
            "$ref": "#/$defs/Action"
          },
          "title": "Options",
          "type": "array"
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
        }
      },
      "required": [
        "options"
      ],
      "title": "ButtonsBlock",
      "type": "object"
    },
    "CardBlock": {
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
        "image": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Image"
        },
        "kind": {
          "const": "card",
          "default": "card",
          "title": "Kind",
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
        "title"
      ],
      "title": "CardBlock",
      "type": "object"
    },
    "ConfirmationBlock": {
      "additionalProperties": false,
      "description": "No prose field exists here either \u2014 the question this block asks\nlives in the message's own `text`, never in the block.",
      "properties": {
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
        "kind": {
          "const": "confirmation",
          "default": "confirmation",
          "title": "Kind",
          "type": "string"
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
        }
      },
      "required": [
        "callback_id"
      ],
      "title": "ConfirmationBlock",
      "type": "object"
    },
    "FormBlock": {
      "additionalProperties": false,
      "properties": {
        "fields": {
          "items": {
            "$ref": "#/$defs/FormField"
          },
          "title": "Fields",
          "type": "array"
        },
        "kind": {
          "const": "form",
          "default": "form",
          "title": "Kind",
          "type": "string"
        },
        "submit_label": {
          "default": "Submit",
          "title": "Submit Label",
          "type": "string"
        }
      },
      "required": [
        "fields"
      ],
      "title": "FormBlock",
      "type": "object"
    },
    "FormField": {
      "additionalProperties": false,
      "properties": {
        "field_id": {
          "title": "Field Id",
          "type": "string"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "field_id",
        "label"
      ],
      "title": "FormField",
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
    }
  },
  "description": "The persistent unit; `id` is the sync cursor (architecture \u00a75).",
  "properties": {
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
    "ButtonsBlock": {
      "additionalProperties": false,
      "description": "No prose field exists here \u2014 see the module docstring. `options`\nabsorbs what a separate `choice` kind would otherwise do; `state` is the\nselected `action_id` once resolved, or `None` while the block is still\nlive.",
      "properties": {
        "kind": {
          "const": "buttons",
          "default": "buttons",
          "title": "Kind",
          "type": "string"
        },
        "options": {
          "items": {
            "$ref": "#/$defs/Action"
          },
          "title": "Options",
          "type": "array"
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
        }
      },
      "required": [
        "options"
      ],
      "title": "ButtonsBlock",
      "type": "object"
    },
    "CardBlock": {
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
        "image": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Image"
        },
        "kind": {
          "const": "card",
          "default": "card",
          "title": "Kind",
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
        "title"
      ],
      "title": "CardBlock",
      "type": "object"
    },
    "ConfirmationBlock": {
      "additionalProperties": false,
      "description": "No prose field exists here either \u2014 the question this block asks\nlives in the message's own `text`, never in the block.",
      "properties": {
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
        "kind": {
          "const": "confirmation",
          "default": "confirmation",
          "title": "Kind",
          "type": "string"
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
        }
      },
      "required": [
        "callback_id"
      ],
      "title": "ConfirmationBlock",
      "type": "object"
    },
    "FormBlock": {
      "additionalProperties": false,
      "properties": {
        "fields": {
          "items": {
            "$ref": "#/$defs/FormField"
          },
          "title": "Fields",
          "type": "array"
        },
        "kind": {
          "const": "form",
          "default": "form",
          "title": "Kind",
          "type": "string"
        },
        "submit_label": {
          "default": "Submit",
          "title": "Submit Label",
          "type": "string"
        }
      },
      "required": [
        "fields"
      ],
      "title": "FormBlock",
      "type": "object"
    },
    "FormField": {
      "additionalProperties": false,
      "properties": {
        "field_id": {
          "title": "Field Id",
          "type": "string"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "field_id",
        "label"
      ],
      "title": "FormField",
      "type": "object"
    },
    "Message": {
      "description": "The persistent unit; `id` is the sync cursor (architecture \u00a75).",
      "properties": {
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
      "description": "No prose field exists here \u2014 see the module docstring. `options`\nabsorbs what a separate `choice` kind would otherwise do; `state` is the\nselected `action_id` once resolved, or `None` while the block is still\nlive.",
      "properties": {
        "kind": {
          "const": "buttons",
          "default": "buttons",
          "title": "Kind",
          "type": "string"
        },
        "options": {
          "items": {
            "$ref": "#/$defs/Action"
          },
          "title": "Options",
          "type": "array"
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
        }
      },
      "required": [
        "options"
      ],
      "title": "ButtonsBlock",
      "type": "object"
    },
    "CardBlock": {
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
        "image": {
          "anyOf": [
            {
              "type": "string"
            },
            {
              "type": "null"
            }
          ],
          "default": null,
          "title": "Image"
        },
        "kind": {
          "const": "card",
          "default": "card",
          "title": "Kind",
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
        "title"
      ],
      "title": "CardBlock",
      "type": "object"
    },
    "ConfirmationBlock": {
      "additionalProperties": false,
      "description": "No prose field exists here either \u2014 the question this block asks\nlives in the message's own `text`, never in the block.",
      "properties": {
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
        "kind": {
          "const": "confirmation",
          "default": "confirmation",
          "title": "Kind",
          "type": "string"
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
        }
      },
      "required": [
        "callback_id"
      ],
      "title": "ConfirmationBlock",
      "type": "object"
    },
    "FormBlock": {
      "additionalProperties": false,
      "properties": {
        "fields": {
          "items": {
            "$ref": "#/$defs/FormField"
          },
          "title": "Fields",
          "type": "array"
        },
        "kind": {
          "const": "form",
          "default": "form",
          "title": "Kind",
          "type": "string"
        },
        "submit_label": {
          "default": "Submit",
          "title": "Submit Label",
          "type": "string"
        }
      },
      "required": [
        "fields"
      ],
      "title": "FormBlock",
      "type": "object"
    },
    "FormField": {
      "additionalProperties": false,
      "properties": {
        "field_id": {
          "title": "Field Id",
          "type": "string"
        },
        "label": {
          "title": "Label",
          "type": "string"
        }
      },
      "required": [
        "field_id",
        "label"
      ],
      "title": "FormField",
      "type": "object"
    }
  },
  "additionalProperties": false,
  "description": "`POST /v1/messages`'s body. `client_msg_id` is the idempotency key \u2014\na replay returns the row it already produced rather than a new one.",
  "properties": {
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

