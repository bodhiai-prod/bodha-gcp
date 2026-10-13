# Identity and configuration API

All paths are relative to `/api`.

## Authentication and account

| Method and path | Access | Input | Outcome / operational note |
| --- | --- | --- | --- |
| `POST /auth/sync` | Firebase identity | Optional name and onboarding school/board fields | Creates or refreshes the application user from Firebase identity; assigns configured initial admin role when eligible. |
| `GET /auth/me` | Auth | — | Returns the normalized current user/profile. |
| `PATCH /auth/profile` | Firebase identity | Required profile fields | Updates the application profile; valid completion unlocks protected workspace access. |
| `GET /auth/account` | Auth | — | Returns account-oriented counts and profile details. |

## Saved paper configurations and blueprints

| Method and path | Access | Input | Outcome / operational note |
| --- | --- | --- | --- |
| `GET /paper-configurations` | Teacher | — | Lists the caller's saved reusable configurations. |
| `POST /paper-configurations` | Teacher | Configuration JSON | Creates a reusable configuration. |
| `PATCH /paper-configurations/{configuration_id}` | Teacher/owner | Partial configuration JSON | Updates the owner's configuration. |
| `DELETE /paper-configurations/{configuration_id}` | Teacher/owner | — | Deletes a saved configuration. |
| `GET /paper-blueprints` | Teacher | — | Lists built-in and available blueprint definitions. |
| `POST /paper-blueprints/analyze` | Teacher | Textbook/blueprint and allocation inputs | Produces a configuration/print-instruction analysis; may read cached instruction state. |
| `POST /papers/configuration-preview` | Teacher | Planned paper configuration | Validates/calculates allocations and a preview before paper creation. |

## Branding

| Method and path | Access | Input | Outcome / operational note |
| --- | --- | --- | --- |
| `GET /branding` | Auth | — | Returns current user's active branding values. |
| `PATCH /branding` | Auth | Branding JSON | Updates active branding values. |
| `POST /branding/image` | Auth | Multipart image | Stores/associates the active header image. |
| `GET /branding/image` | Auth | — | Streams or returns the active header image. |
| `GET /branding-presets` | Auth | — | Lists the caller's reusable branding presets. |
| `POST /branding-presets` | Auth | Preset JSON | Creates a preset. |
| `PATCH /branding-presets/{branding_id}` | Auth/owner | Partial preset JSON | Updates the owned preset. |
| `DELETE /branding-presets/{branding_id}` | Auth/owner | — | Deletes the preset and its association. |
| `POST /branding-presets/{branding_id}/image` | Auth/owner | Multipart image | Uploads/replaces a preset image. |
| `GET /branding-presets/{branding_id}/image` | Auth/owner | — | Retrieves preset image content. |
| `DELETE /branding-presets/{branding_id}/image` | Auth/owner | — | Removes preset image content. |
