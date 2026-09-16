import webview
from app.web_api import WebAPI
from app.db.schema import init_db

if __name__ == "__main__":
    init_db()

    api = WebAPI()

    window = webview.create_window(
        title="Office Manager",
        url="http://localhost:5173",  # Vite dev server
        js_api=api
    )

    webview.start()
