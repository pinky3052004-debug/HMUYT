import os
import io
import re
import json
from datetime import datetime, timedelta, timezone
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaFileUpload, MediaIoBaseUpload

# --- Google Drive Service ---
def get_gdrive_service():
    DRIVE_SCOPES = ['https://www.googleapis.com/auth/drive']
    creds_data = json.loads(os.environ['GDRIVE_CREDENTIALS'])
    creds = Credentials.from_authorized_user_info(creds_data, DRIVE_SCOPES)
    return build('drive', 'v3', credentials=creds)

# --- YouTube Service ---
def get_youtube_service():
    YOUTUBE_SCOPES = ['https://www.googleapis.com/auth/youtube.force-ssl']
    creds_data = json.loads(os.environ['YOUTUBE_CREDENTIALS'])
    creds = Credentials.from_authorized_user_info(creds_data, YOUTUBE_SCOPES)
    return build('youtube', 'v3', credentials=creds)

# --- uploaded.txt ကို စီမံခန့်ခွဲသည့် Function များ ---
def get_or_create_uploaded_file(drive_service, folder_id):
    """uploaded.txt ဖိုင်ကို ရှာဖွေပြီး ID ကိုပြန်ပေးသည်၊ မရှိပါက အသစ်ဆောက်သည်"""
    query = f"'{folder_id}' in parents and name='uploaded.txt' and trashed=false"
    results = drive_service.files().list(q=query, fields="files(id)").execute()
    files = results.get('files', [])
    
    if files:
        return files[0]['id']
    else:
        file_metadata = {
            'name': 'uploaded.txt',
            'mimeType': 'text/plain',
            'parents': [folder_id]
        }
        media = MediaIoBaseUpload(io.BytesIO(b""), mimetype='text/plain', resumable=True)
        new_file = drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        return new_file['id']

def get_uploaded_videos(drive_service, file_id):
    """uploaded.txt ထဲမှ တင်ပြီးသား ဗီဒီယိုနာမည်များကို List အဖြစ် ဖတ်ယူသည်"""
    try:
        request = drive_service.files().get_media(fileId=file_id)
        file_content = request.execute()
        return file_content.decode('utf-8').splitlines()
    except Exception:
        return []

def append_to_uploaded_file(drive_service, file_id, video_name, current_list):
    """uploaded.txt ထဲသို့ ဗီဒီယိုနာမည်အသစ်ကို လှမ်းထည့်ပြီး Drive ပေါ်တွင် Update လုပ်သည်"""
    current_list.append(video_name)
    new_content = "\n".join(current_list) + "\n"
    
    media = MediaIoBaseUpload(io.BytesIO(new_content.encode('utf-8')), mimetype='text/plain', resumable=True)
    drive_service.files().update(fileId=file_id, media_body=media).execute()

# --- main.json မှ Metadata ရှာဖွေဖတ်ရှုသည့် Function ---
def get_metadata_for_video(drive_service, folder_id, video_name):
    """ဗီဒီယိုနာမည် (ဥပမာ 00001.mp4) နဲ့ ကိုက်ညီတဲ့ ID (ဥပမာ 1 သို့မဟုတ် 00001) ကို main.json ထဲမှာ ရှာဖွေသည်"""
    base_name = os.path.splitext(video_name)[0]  # "00001"
    match = re.search(r'(\d+)', base_name)
    file_num = int(match.group(1)) if match else None  # 1

    # ၁။ ပထမဦးစွာ main.json ကို ရှာဖွေမည်
    query = f"'{folder_id}' in parents and name='main.json' and trashed=false"
    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get('files', [])

    # main.json မရှိပါက အခြား json ဖိုင်များကိုပါ ရှာမည်
    if not files:
        query_all = f"'{folder_id}' in parents and name contains 'json' and trashed=false"
        results_all = drive_service.files().list(q=query_all, fields="files(id, name)").execute()
        files = results_all.get('files', [])

    for file in files:
        try:
            request = drive_service.files().get_media(fileId=file['id'])
            content = request.execute()
            data = json.loads(content.decode('utf-8'))
            
            # JSON ထဲမှာ List ဖြစ်နေခြင်း (ဥပမာ [{"id": 1, ...}, {"id": 2, ...}])
            if isinstance(data, list):
                for item in data:
                    item_id = item.get('id')
                    if item_id is not None and (str(item_id) == str(file_num) or str(item_id) == base_name or item.get('video_name') == video_name):
                        return item
            # JSON ထဲမှာ Dict ဖြစ်နေခြင်း (ဥပမာ {"1": {...}} သို့မဟုတ် {"id": 1, ...})
            elif isinstance(data, dict):
                if str(data.get('id')) == str(file_num) or str(data.get('id')) == base_name:
                    return data
                elif file_num is not None and str(file_num) in data and isinstance(data[str(file_num)], dict):
                    return data[str(file_num)]
                elif str(base_name) in data and isinstance(data[str(base_name)], dict):
                    return data[str(base_name)]
        except Exception:
            continue
            
    return None

# --- Main Logic ---
def main():
    folder_id = os.environ.get('GDRIVE_FOLDER_ID')
    if not folder_id:
        print("Error: GDRIVE_FOLDER_ID ကို Environment Variable / Secrets တွင် မသတ်မှတ်ရသေးပါ။")
        return

    drive_service = get_gdrive_service()
    youtube_service = get_youtube_service()

    txt_file_id = get_or_create_uploaded_file(drive_service, folder_id)
    uploaded_videos_list = get_uploaded_videos(drive_service, txt_file_id)

    print("သတ်မှတ်ထားသော Folder ID အတွင်းမှ ဖိုင်များကို စစ်ဆေးနေသည်...")

    # ၁။ Drive ထဲမှ .mp4 ဖိုင်များအားလုံးကို ရှာဖွေခြင်း
    items = []
    page_token = None
    query = f"'{folder_id}' in parents and mimeType='video/mp4' and trashed=false"
    
    while True:
        results = drive_service.files().list(
            q=query,
            fields="nextPageToken, files(id, name)",
            pageToken=page_token
        ).execute()
        items.extend(results.get('files', []))
        page_token = results.get('nextPageToken')
        if not page_token:
            break

    pending_videos = []
    for item in items:
        name = item['name']
        if name not in uploaded_videos_list:
            match = re.search(r'(\d+)', name)
            file_num = int(match.group(1)) if match else float('inf')
            pending_videos.append((file_num, item))

    # 🔄 နံပါတ်အကြီးမှ အငယ်သို့ စီစဉ်ခြင်း (ဥပမာ 00010.mp4, 00009.mp4 ... 00001.mp4)
    pending_videos.sort(key=lambda x: x[0], reverse=True)

    if not pending_videos:
        print("တင်ရန် ဗီဒီယိုအသစ် မတွေ့ရှိပါ။")
        return

    # တစ်ကြိမ်လျှင် အများဆုံး ၄ ဖိုင်
    videos_to_upload = pending_videos[:3]
    
    schedule_slots = [ 
        (11, 30),
        (16, 30), 
        (19, 30)
    ]

    mmt_tz = timezone(timedelta(hours=6, minutes=30))
    now_mmt = datetime.now(mmt_tz)

    for index, (file_num, item) in enumerate(videos_to_upload):
        video_id = item['id']
        video_name = item['name']
        local_filename = f"temp_{video_name}"
        base_name = os.path.splitext(video_name)[0]
        local_thumb_filename = f"temp_{base_name}.jpg"

        hour, minute = schedule_slots[index]
        slot_time = now_mmt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        if slot_time <= now_mmt:
            print(f"\n⚠️ [{index+1}/{len(videos_to_upload)}] Skip - {video_name} အတွက် MMT {hour:02d}:{minute:02d} အချိန်သည် လွန်သွားခဲ့ပြီဖြစ်၍ မတင်တော့ပါ။")
            continue

        utc_slot_time = slot_time.astimezone(timezone.utc)
        publish_at_iso = utc_slot_time.strftime('%Y-%m-%dT%H:%M:%SZ')

        print(f"\n[{index+1}/{len(videos_to_upload)}] ဒေါင်းလုဒ်ဆွဲနေသည်: {video_name}")

        try:
            # ၂။ Google Drive မှ Video ဖိုင် Download ရယူခြင်း
            request = drive_service.files().get_media(fileId=video_id)
            with io.FileIO(local_filename, 'wb') as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    status, done = downloader.next_chunk()

            # (Optional) သက်ဆိုင်ရာ Thumbnail (ဥပမာ 00001.jpg) ကိုပါ ရှာဖွေ ဒေါင်းလုဒ်ဆွဲရန်
            thumb_query = f"'{folder_id}' in parents and name='{base_name}.jpg' and trashed=false"
            thumb_results = drive_service.files().list(q=thumb_query, fields="files(id)").execute()
            thumb_files = thumb_results.get('files', [])
            has_thumbnail = False

            if thumb_files:
                thumb_id = thumb_files[0]['id']
                thumb_request = drive_service.files().get_media(fileId=thumb_id)
                with io.FileIO(local_thumb_filename, 'wb') as fh:
                    downloader = MediaIoBaseDownload(fh, thumb_request)
                    done = False
                    while not done:
                        _, done = downloader.next_chunk()
                has_thumbnail = True
                print(f"🖼️ Thumbnail တွေ့ရှိပြီး ဒေါင်းလုဒ်လုပ်ပြီးပါပြီ: {base_name}.jpg")

            # main.json မှ အချက်အလက်များကို ဆွဲထုတ်ရန်
            meta = get_metadata_for_video(drive_service, folder_id, video_name)
            
            if meta:
                video_title = meta.get('title', '#shorts #trending')
                video_desc = meta.get('description', '#shorts')
                video_tags = meta.get('video_tags', ['shorts', 'trending'])
                print(f"✨ main.json မှ Metadata တွေ့ရှိပြီး - Title: {video_title[:30]}...")
            else:
                video_title = "#hsu #beautiful #foryou #dance #shorts #youtubeshorts #အကိတ်တလိုင်း #fypシ゚viral #TrendingMM"
                video_desc = "#hsu #beautiful #2d3d #live #foryou #dance #shorts #youtubeshorts #အကိတ်တလိုင်း #fypシ゚viral #TrendingMM #fyp"
                video_tags = ['hsu', 'myanmar tiktok', 'smart', 'shorts', 'trending']
                print("⚠️ main.json ထဲတွင် သက်ဆိုင်ရာ ID မတွေ့ရှိရပါ၊ Default ပုံစံဖြင့် တင်ပါမည်။")

            # ၃။ YouTube သို့ Upload တင်ခြင်း
            print(f"YouTube တွင် Schedule သတ်မှတ်နေသည် - အချိန်: MMT {slot_time.strftime('%H:%M')} (UTC {publish_at_iso})")
            body = {
                'snippet': {
                    'title': video_title,
                    'description': video_desc,
                    'categoryId': '24', # Entertainment
                    'tags': video_tags
                },
                'status': {
                    'privacyStatus': 'private',
                    'publishAt': publish_at_iso,
                    'selfDeclaredMadeForKids': False
                }
            }

            media = MediaFileUpload(local_filename, chunksize=-1, resumable=True, mimetype='video/mp4')
            upload_request = youtube_service.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media
            )

            response = None
            while response is None:
                status, response = upload_request.next_chunk()

            yt_video_id = response['id']
            print(f"Schedule လုပ်ဆောင်ချက် အောင်မြင်သည်။ Video ID: {yt_video_id}")

            # ၄။ Thumbnail တင်ခြင်း (ရှိခဲ့လျှင်)
            if has_thumbnail:
                try:
                    youtube_service.thumbnails().set(
                        videoId=yt_video_id,
                        media_body=MediaFileUpload(local_thumb_filename)
                    ).execute()
                    print(f"🖼️ Thumbnail ကို YouTube သို့ အောင်မြင်စွာ တင်ပြီးပါပြီ။")
                except Exception as thumb_err:
                    print(f"⚠️ Thumbnail တင်ရာတွင် အမှားဖြစ်ပေါ်ခဲ့သည်: {thumb_err}")

            # ၅။ uploaded.txt တွင် မှတ်တမ်းတင်ခြင်း
            append_to_uploaded_file(drive_service, txt_file_id, video_name, uploaded_videos_list)
            print(f"uploaded.txt ထဲသို့ မှတ်တမ်းတင်ပြီးပါပြီ: {video_name}")

        except Exception as e:
            print(f"❌ Error ဖြစ်ပေါ်ခဲ့သည် ({video_name}): {e}")

        finally:
            # Local Temp ဖိုင်များ ရှင်းလင်းခြင်း
            if os.path.exists(local_filename):
                os.remove(local_filename)
            if os.path.exists(local_thumb_filename):
                os.remove(local_thumb_filename)

if __name__ == '__main__':
    main()
