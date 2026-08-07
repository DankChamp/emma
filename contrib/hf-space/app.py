"""Entry point for the Hugging Face *Gradio* Space (Docker SDK is paid now).

The Gradio SDK is free but expects a gradio app on port 7860. Emma is a
plain FastAPI app, so we do it the other way round: build Emma's app
(importing main also restores data/*.db from the HF Dataset backup), mount
a one-card Gradio page onto it at /gradio to keep HF's runtime happy, and
serve the combined app with uvicorn on the port HF probes.
"""
import os

# On Spaces, gradio's SSR mode spawns a Node server that grabs port 7860
# during app startup, colliding with the uvicorn bind below. Must be set
# before gradio is imported.
os.environ["GRADIO_SSR_MODE"] = "false"

import spaces  # noqa: F401
import gradio as gr

import main  # noqa: E402 - side effects on purpose: restore dbs, build app


# @spaces.GPU is a class in current runtimes; instantiate it
# (bare @spaces.GPU would hand the scanner an instance, not a callable fn,
#  so ZeroGPU's startup scan can't see it and the Space is killed with
#  "No @spaces.GPU function detected during startup").
@spaces.GPU(duration=60)
def _noop(text: str = "ping") -> str:
    return "ok"


with gr.Blocks(title="Emma") as demo:
    gr.Markdown(
        "# 🌸 Emma is running\n\n"
        "- [Open the web UI](/ui/)\n"
        "- [Health check](/status)\n\n"
        "The Telegram bot is polling from this Space."
    )
    # The HF ZeroGPU runtime's startup scan only walks Gradio event handlers
    # bound to @spaces.GPU-decorated functions (a bare, unattached GPU function
    # is ignored, which kills the Space with "No @spaces.GPU function detected").
    # Wire _noop to a (hidden) button click with real inputs/outputs so GPU
    # allocation succeeds, while keeping the landing card tidy.
    _gpu_in = gr.Textbox(value="ping", visible=False)
    _gpu_out = gr.Textbox(visible=False)
    _gpu_trigger = gr.Button("health check", visible=False)
    _gpu_trigger.click(fn=_noop, inputs=_gpu_in, outputs=_gpu_out)

# Mounted at "/" so HF's health check finds gradio's /config endpoint.
# Emma's own routes (/, /status, /ui, ...) were registered first and take
# precedence; the gradio mount only catches what's left.
app = gr.mount_gradio_app(main.app, demo, path="/")

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("GRADIO_SERVER_PORT", os.environ.get("PORT", 7860)))
    uvicorn.run(app, host="0.0.0.0", port=port)
