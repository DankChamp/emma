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


@spaces.GPU  # ZeroGPU requires at least one @spaces.GPU to start
def _noop() -> None:
    pass

with gr.Blocks(title="Emma") as demo:
    gr.Markdown(
        "# 🌸 Emma is running\n\n"
        "- [Open the web UI](/ui/)\n"
        "- [Health check](/status)\n\n"
        "The Telegram bot is polling from this Space."
    )

# Mounted at "/" so HF's health check finds gradio's /config endpoint.
# Emma's own routes (/, /status, /ui, ...) were registered first and take
# precedence; the gradio mount only catches what's left.
app = gr.mount_gradio_app(main.app, demo, path="/")

if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("GRADIO_SERVER_PORT", os.environ.get("PORT", 7860)))
    uvicorn.run(app, host="0.0.0.0", port=port)
