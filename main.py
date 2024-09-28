import json
from fastapi.responses import JSONResponse
import httpx
import asyncio
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect, HTTPException
from pydantic import BaseModel
import logging
from typing import Dict, Any, List
import uuid  # أضف هذا الاستيراد في بداية الملف

app = FastAPI()

# إعداد تسجيل الدخول بمستوى أكثر تفصيلاً
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

# قائمة للاحتفاظ بعملاء WebSocket المتصلين
websocket_clients: List[WebSocket] = []

# قائمة لتخزين بيانات المباريات
matches_data: List[Dict[str, Any]] = []

# متغير عام لتخزين المعرف الحالي للمسار
current_route_id = None

# إضافة متغير للتحكم في حالة بيانات روابط البث
stream_links_available = asyncio.Event()

# قائمة لتخزين بيانات روابط البث
stream_links_data: Dict[str, Any] = {}

async def log_route(request: Request):
    global current_route_id
    route_name = request.url.path
    
    # إنشاء معرف جديد فقط إذا كان المسار مختلفًا
    if route_name != getattr(log_route, 'last_route', None):
        current_route_id = str(uuid.uuid4())
        log_route.last_route = route_name

    logging.info(f"User accessed: {route_name} (ID: {current_route_id})")
    
    # إرسال إشعار إلى جميع عملاء WebSocket المتصلين
    for client in websocket_clients:
        await client.send_text(json.dumps({
            "id": current_route_id,
            "message": f"Notification from server: Route accessed: {route_name}"
        }))

class MatchData(BaseModel):
    team1: str
    team2: str
    score: str

# إضافة متغير للتحكم في حالة البيانات
data_available = asyncio.Event()

@app.get("/matches")
async def get_matches(request: Request):
    await log_route(request)
    logging.info("Waiting for data...")
    await data_available.wait()
    
    logging.info("Data available, proceeding...")
    if not matches_data:
        raise HTTPException(status_code=404, detail="لم يتم العثور على بيانات للمباريات")
    
    logging.info(f"Returning {len(matches_data)} matches")
    return {"matches": matches_data}

@app.post("/matches")
async def post_matches(request: Request):
    await log_route(request)
    try:
        data = await request.json()
        logging.debug(f"Received POST data: {data}")
        global matches_data
        matches_data = data.get("matches", [])
        logging.info(f"Stored {len(matches_data)} matches")
        
        # تعيين الحدث لإشارة أن البيانات متوفرة الآن
        data_available.set()
        logging.info("Data availability flag set")
        
        return {"status": "success", "message": f"تم استلام {len(matches_data)} مباراة"}
    except Exception as e:
        logging.error(f"Error processing POST request: {str(e)}")
        return {"status": "error", "message": str(e)}

@app.get("/admins")
async def get_admins(request: Request):
    await log_route(request)
    route_name = request.url.path
    return {"route": route_name}

@app.get("/stream_links/{watch_id:path}")
async def get_stream_links(watch_id: str, request: Request):
    await log_route(request)
    logging.info(f"Requesting stream links for watch_id: {watch_id}")
    
    # تحقق مما إذا كانت البيانات موجودة بالفعل
    if watch_id in stream_links_data:
        logging.info(f"Stream links for watch_id: {watch_id} found in cache")
        return stream_links_data[watch_id]
    
    # إذا لم تكن البيانات موجودة، انتظر حتى تصبح متاحة
    logging.info(f"Waiting for stream links data for watch_id: {watch_id}")
    await stream_links_available.wait()
    
    if watch_id in stream_links_data:
        return stream_links_data[watch_id]
    else:
        raise HTTPException(status_code=404, detail="لم يتم العثور على روابط البث لهذه المباراة")

@app.post("/stream_links/{watch_id:path}")
async def post_stream_links(watch_id: str, request: Request):
    try:
        # استقبال البيانات كـ JSON
        data = await request.json()
        
        # تخزين البيانات في القاموس
        stream_links_data[watch_id] = data
        
        # طباعة البيانات المستلمة بتنسيق مقروء
        logging.info(f"تم استلام بيانات للمشاهدة ID: {watch_id}")
        logging.info("البيانات المستلمة:")
        logging.info(json.dumps(data, indent=2, ensure_ascii=False))
        
        # إرجاع رسالة تأكيد
        return JSONResponse(content={"status": "success", "message": "تم استلام وتخزين البيانات بنجاح"}, status_code=200)
    except Exception as e:
        # في حالة حدوث خطأ
        logging.error(f"حدث خطأ أثناء معالجة الطلب: {str(e)}")
        raise HTTPException(status_code=400, detail=f"خطأ في معالجة الطلب: {str(e)}")

@app.get("/stream_links/{watch_id:path}")
async def get_stream_links(watch_id: str):
    if watch_id in stream_links_data:
        data = stream_links_data[watch_id]
        logging.info(f"تم طلب البيانات للمشاهدة ID: {watch_id}")
        logging.info("البيانات المُرجعة:")
        logging.info(json.dumps(data, indent=2, ensure_ascii=False))
        return JSONResponse(content=data)
    else:
        raise HTTPException(status_code=404, detail="لم يتم العثور على بيانات لهذا المعرف")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    websocket_clients.append(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            logging.info(f"Received WebSocket data: {data}")
            # استخدام المعرف الحالي للمسار بدلاً من إنشاء معرف جديد
            await websocket.send_text(json.dumps({
                "id": current_route_id,
                "message": f"Route accessed: {data}"
            }))
    except WebSocketDisconnect:
        websocket_clients.remove(websocket)
        logging.info("WebSocket client disconnected")
    except Exception as e:
        logging.error(f"WebSocket error: {e}")
    finally:
        await websocket.close()

@app.on_event("startup")
async def startup_event():
    # إعادة تعيين حالة البيانات عند بدء التطبيق
    global data_available, stream_links_available
    data_available.clear()
    stream_links_available.clear()
    logging.info("Application started, data availability reset")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
