from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import os, time, re, requests, dateparser
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()
templates = Jinja2Templates(directory="templates")

TWILIO_ACCOUNT_SID = os.getenv("SID")
TWILIO_AUTH_TOKEN = os.getenv("AUTH")
FROM_NUMBER = os.getenv("FROM")
TO_NUMBER = os.getenv("TO")
ASSEMBLYAI_API_KEY = os.getenv("API")


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Serve the HTML page."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.post("/make_call")
async def make_call():
    """Trigger the Twilio call and transcription process."""
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

        call = client.calls.create(
            twiml='<Response><Say voice="alice">Hello! Please tell me your name, preferred date, and time for appointment after the beep. This call will be recorded.</Say><Record maxLength="20" /></Response>',
            to=TO_NUMBER,
            from_=FROM_NUMBER,
            record=True
        )
        print(f"Call SID: {call.sid}")

        time.sleep(90)
        recording_url = None
        while recording_url is None:
            recordings = client.recordings.list(call_sid=call.sid)
            if recordings:
                recording_sid = recordings[0].sid
                recording_url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Recordings/{recording_sid}.mp3"
            else:
                time.sleep(5)

        resp = requests.get(recording_url, auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN))
        local_file = "recording.mp3"
        with open(local_file, "wb") as f:
            f.write(resp.content)

        headers = {"authorization": ASSEMBLYAI_API_KEY}
        with open(local_file, "rb") as f:
            upload_resp = requests.post("https://api.assemblyai.com/v2/upload", headers=headers, data=f)
        audio_url = upload_resp.json()["upload_url"]

        transcript_req = requests.post(
            "https://api.assemblyai.com/v2/transcript",
            json={"audio_url": audio_url},
            headers=headers
        )
        transcript_id = transcript_req.json()["id"]

        while True:
            poll = requests.get(f"https://api.assemblyai.com/v2/transcript/{transcript_id}", headers=headers)
            status = poll.json()["status"]
            if status == "completed":
                transcript_text = poll.json()["text"]
                break
            elif status == "error":
                return JSONResponse({"error": poll.json()["error"]}, status_code=500)
            else:
                time.sleep(5)

        name_pattern = r"(?:my name is|i am|i'm|this is)\s+([A-Za-z ]+)"
        name_match = re.search(name_pattern, transcript_text, re.IGNORECASE)
        name = name_match.group(1).strip() if name_match else "Unknown"

        date_time_pattern = (
            r"(?:on|for)?\s*"
            r"(\d{1,2}(?:st|nd|rd|th)?\s+of\s+[A-Za-z]+|"
            r"\d{1,2}\s+[A-Za-z]+|"
            r"[A-Za-z]+\s+\d{1,2})"
            r"(?:\s+at\s+([\d: ]+\s*(?:am|pm)?))?"
        )
        matches = re.search(date_time_pattern, transcript_text, re.IGNORECASE)

        date_text = matches.group(1) if matches else None
        time_text = matches.group(2) if matches and matches.lastindex >= 2 else None

        dt = None
        if date_text:
            dt = dateparser.parse(
                date_text + (" " + time_text if time_text else ""),
                settings={"PREFER_DATES_FROM": "future"}  # interpret as future date if ambiguous
            )
        date = str(dt.date()) if dt else "Unknown"
        time_ = str(dt.time()) if dt else "Unknown"
        return JSONResponse({
            "message": "Call completed and transcribed!",
            "name": name,
            "date": str(date),
            "time": str(time_),
            "transcript": transcript_text
        })

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
