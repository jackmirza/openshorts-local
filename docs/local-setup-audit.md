# Local setup audit — 20 November 2026

Scope: the OpenShorts application and the linked GPU setup guide, with a focus
on a Windows laptop with 8 GB NVIDIA VRAM, gameplay clipping and no additional
paid services. This is a focused engineering review, not a claim that every
feature or dependency is free from defects.

Reviewed application base: `mutonby/openshorts` commit
`408f164a5909731176886a08b5a848f522feba41`. The upstream MIT license and notices
are retained.

## Findings and changes

| Finding | Change in this fork |
| --- | --- |
| The guide closes its `.env` heredoc before three settings | The local Compose profile declares its settings directly, so they reach the containers |
| Mounting the whole checkout over `/app` hides the image's downloaded YOLO weights | The standalone local profile mounts only runtime data and the model cache |
| Several default workers and resident models can compete for laptop VRAM | One job, one clip worker and one ASR slot; Ollama releases its model after requests |
| The transcript picker cannot understand mostly silent gameplay; automatic vision needs Gemini | An opt-in local timestamp workspace handles gameplay without a model or API |
| The upstream app imports Unix file locks | Native Windows entry point for the lightweight workspace; full application tests run under Linux |
| Windows checkout line endings break the Linux FFmpeg wrapper | Git attributes keep executable shell scripts LF-terminated |
| Redirected Windows output can fail when the encoder logs Unicode | UTF-8 worker startup and output streams |
| A child worker can exit when its setup terminal is closed | The Windows process service launches a hidden independent worker |
| Dashboard, composition and renderer locks contain different Remotion versions | Direct Remotion packages aligned to 4.0.541 |
| The dashboard and renderer use Node 18 images | Verified Node 24 image digests for the application containers |
| Package audit reports dashboard and renderer advisories | Updated Vite/React plugin, compatible lockfile security fixes, patched renderer transitive dependencies and native `randomUUID` |
| The renderer ignores the configured browser and does not explicitly forward render input props | The browser executable is passed to composition selection/rendering, together with the render props |
| The dashboard warns about a missing publishing key even for clipping | Publishing credentials are no longer part of the clipping-key warning |

## Local workspace boundaries

The workspace accepts local file uploads rather than downloading arbitrary
URLs. It checks readable video metadata, IDs, ranges and source duration;
rejects unrelated browser origins; serializes rendering; and exposes only
completed MP4 outputs. Hook text becomes a PNG rather than executable filter
syntax. Runtime files and settings are ignored by Git. It does not upload to
YouTube or enable a paid service.

The native workspace and optional AI stack are distinct. The working native
mode does not select moments automatically or create automatic captions.
The optional full GPU image and complete large-model workflow were not
validated end to end during this setup; their configuration is provided for
an explicit later installation. An initial large-image build was stopped
in favor of finishing and testing the lightweight gameplay workflow.

## Validation

- Local workspace request/validation tests.
- Windows local workspace: 18 tests passed. The full Linux suite passed with
  2,379 passed and 90 skipped (optional models/services absent locally).
- Real upload, render, download and full decoder checks using a generated
  moving test pattern, in both fit and crop modes at 1080×1920/30 fps.
- NVIDIA NVENC used for both native export checks; audio retained in the fit
  test and absent in the muted crop test.
- Browser file selection, upload, clip creation and download link verified.
- Dashboard production build and lint.
- Renderer TypeScript build and two composition-metadata tests using an
  installed Chrome executable.
- Dependency audits for the dashboard, compositions and renderer. Counts are
  point-in-time reports, not a security guarantee.

The full upstream test suite is Linux-specific. A direct Windows run exposed
missing `fcntl` during collection, so Linux verification is performed in an
isolated Ubuntu environment. GitHub CI is also checked after the push.

The existing GitHub workflow is retained: the saved account token does not
permit workflow-file edits. The application still runs its upstream backend
and frontend checks there; frontend lint and renderer tests also run locally.

Sources: [application](https://github.com/mutonby/openshorts),
[setup guide](https://github.com/Mayankgg01/Guide-of-OpenShorts).
