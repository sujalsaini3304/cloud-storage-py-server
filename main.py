from fastapi import FastAPI ,  File , Body ,BackgroundTasks , UploadFile , Form , HTTPException , Query
import cloudinary
import cloudinary.uploader
import os
import base64
import io
import random
from fastapi_mail import FastMail, MessageSchema, MessageType , ConnectionConfig
import bcrypt
from pydantic import BaseModel ,  EmailStr , Field
from dotenv import load_dotenv
from typing import List
from motor.motor_asyncio import AsyncIOMotorClient
from bson import ObjectId
from datetime import datetime
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.datastructures import FormData
from starlette.formparsers import MultiPartParser
from typing import Dict
import cloudinary.api
import pytz

app = FastAPI()
load_dotenv()
tz = pytz.timezone("UTC")
DESIRED_TIMEZONE = pytz.timezone("Asia/Kolkata")  


def convert_to_local_timezone(utc_time_str: str, from_format="%Y-%m-%d %H:%M:%S") -> str:
    try:
        utc_time = datetime.strptime(utc_time_str, from_format)
        utc_time = pytz.utc.localize(utc_time)
        local_time = utc_time.astimezone(DESIRED_TIMEZONE)
        return local_time.strftime(from_format)
    except Exception as e:
        return utc_time_str  # fallback to original if parsing fails



#Email config
conf = ConnectionConfig(
    MAIL_USERNAME=os.getenv("MAIL_USERNAME"),
    MAIL_PASSWORD=os.getenv("MAIL_PASSWORD"),
    MAIL_FROM=os.getenv("MAIL_FROM"),
    MAIL_PORT=os.getenv("MAIL_PORT"),
    MAIL_SERVER=os.getenv("MAIL_SERVER"),
    USE_CREDENTIALS=True,
    VALIDATE_CERTS=True,
    MAIL_STARTTLS=True,
    MAIL_SSL_TLS=False,
)


# Helper function to convert object _id of mongodb to string format
def serialize_doc(doc):
    doc = dict(doc)  # ensure it's a mutable dict
    doc["id"] = str(doc["_id"])
    del doc["_id"]
    return doc


cloudinary.config(
    cloud_name=os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key=os.getenv('CLOUDINARY_API_KEY'),
    api_secret=os.getenv('CLOUDINARY_API_SECRET')
)

client = AsyncIOMotorClient(os.getenv('MONGODB_URI'))
db = client["cloudStorageProject"]

@app.get("/")
def root():
    return {"message" : "Server is running"}

@app.get("/ping")
def ping():
    return {"status" : "ok"}

def remove_key(d, key):
    d.pop(key, None)

class EmailSchema(BaseModel):
    email: EmailStr
    subject: str
    body: str


class UserEmail(BaseModel):
    email: EmailStr

@app.post("/api/send/email/password/reset")
async def emailExist(payload : UserEmail ,background_tasks: BackgroundTasks):
    collection = db["user"]
    documents = await collection.find_one({"email": payload.email})
    if documents :
        documents["_id"] = str(documents["_id"])
        remove_key(documents , "password")
        title = "Confirmation email for password reset"
        code = random.randint(1000, 9999)
        async def sendConfirmationThroughemail(email_data:EmailSchema, background_tasks: BackgroundTasks):
           message = MessageSchema(
           subject=email_data.subject,
           recipients=[email_data.email],
           body=email_data.body,
           subtype=MessageType.html
           )
           fm = FastMail(conf)
           background_tasks.add_task(fm.send_message, message)

        def generate_verification_email_body(code: str) -> str:
            return f"""
                <html>
                <body style="font-family: Arial, sans-serif; background-color: #f4f4f7; padding: 20px; color: #333;">
                <table width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width: 600px; margin: auto; background-color: #ffffff; padding: 30px; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.05);">
                <tr>
                <td>
                <h2 style="color: #2089dc; margin-top: 0;">Reset Your Password</h2>
                <p style="font-size: 16px;">Hi there,</p>
                <p style="font-size: 16px;">
                We received a request to reset the password associated with this email address. 
                Use the verification code below to proceed:
                </p>
                <div style="font-size: 28px; font-weight: bold; margin: 30px 0; color: #2089dc; text-align: center; letter-spacing: 2px;">
                {code}
                </div>
                <p style="font-size: 14px; color: #666;">
                If you didn’t request a password reset, you can safely ignore this email.
                </p>
                <br/>
                <p style="font-size: 14px;">Thanks,<br/><strong>Cloudee Team</strong></p>
                </td>
                </tr>
                </table>
                </body>
                </html>
                """

        await sendConfirmationThroughemail(
            EmailSchema(
            email = payload.email ,
            subject = title,
            body = generate_verification_email_body(code)
            ),
             background_tasks
        )
    
        return {
            "message" : "Password reset email send successfully.",
            "code" : code ,
            "flag":True ,
            "data" : documents
        } 
    
    else:
        return {
            "message" : "User not found.",
            "flag":False ,
            "data" : None
            }
    
class UserInfo(BaseModel):
    email : EmailStr
    password : str

@app.post("/api/update/password")
async def  update_password(payload : UserInfo ):
    collection = db["user"]
    user = await collection.find_one({"email": payload.email})
    if not user :
       raise HTTPException(status_code=404, detail="User not found.")

    if bcrypt.checkpw(payload.password.encode("utf-8"), user["password"].encode("utf-8")):
       raise HTTPException(status_code=400, detail="Using the old password.")

    hashed_password = bcrypt.hashpw(payload.password.encode('utf-8'), bcrypt.gensalt())
    result = await collection.update_one(
        {"email": payload.email},
        {"$set": {"password": hashed_password.decode('utf-8')}}
    )
    
    if result.modified_count > 0:
        return {
            "status" : "Success",
            "message" : "Password updated successfully.",
            "flag" : True
        }
    else:
        return {
            "status" : "Failed",
            "message" : "Password not changed.",
            "flag" : False
        }


@app.post("/api/delete/user")
async def deleteUser(payload : UserEmail):
    collection = db["asset"]
    deleted_from_cloudinary = []
    deleted_from_db = []

    documents = await collection.find({"email": payload.email}).to_list(length=None)
     
    newListOfImageObject = [
        {
            "mongo_id": str(doc["_id"]),
            "public_id": doc["public_id"]
        }
        for doc in documents
    ]

    for item in newListOfImageObject:
        try:
            # Delete from Cloudinary
            cloudinary.uploader.destroy(item["public_id"], invalidate=True)
            deleted_from_cloudinary.append(item["public_id"])

            # Delete from MongoDB by _id
            delete_result = await collection.delete_one({"_id": ObjectId(item["mongo_id"])})
            if delete_result.deleted_count:
                deleted_from_db.append(item["mongo_id"])

        except Exception as e:
            print(f"Error deleting {item['public_id']}: {e}")
            continue

    try:
        folder_name = f"CloudStorageProject/User/Data/{payload.email}"
        cloudinary.api.delete_folder(folder_name)
    except Exception as e:
        print(f"Failed to delete folder '{folder_name}': {e}")    

    collection_ = db["user"]
    result = await collection_.delete_one({"email": payload.email})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    return { "message" : "success" ,
             "detail": f"User with email '{payload.email}' deleted.",
              "deleted_from_cloudinary": deleted_from_cloudinary,
              "deleted_from_mongodb": deleted_from_db,
              "total_requested": len(newListOfImageObject),
              "success_count": len(deleted_from_db) 
            }



class User(BaseModel):
    name:str
    email:str
    password:str



@app.post("/api/create/user")
async def create_user(payload: User):
    collection = db["user"]

    # 🔐 Check if the user already exists
    existing_user = await collection.find_one({"email": payload.email})
    if existing_user:
        raise HTTPException(status_code=409, detail="User already exists")

    hashed_password = bcrypt.hashpw(payload.password.encode('utf-8'), bcrypt.gensalt())

    item = {
        "username": payload.name,
        "email": payload.email,
        "password": hashed_password.decode('utf-8'),  # Store as string
        "is_email_verified": True,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }

    db_response = await collection.insert_one(item)

    return {
        "message": "User created successfully",
        "id": str(db_response.inserted_id)
    }


class UserDetail(BaseModel):
    email : EmailStr
    password : str
    

@app.post("/api/verify/user")
async def fetch_data(userData: UserDetail):
    collection = db["user"]
    document = await collection.find_one({"email": userData.email})

    if document:
        document["_id"] = str(document["_id"])
        if(bcrypt.checkpw(userData.password.encode('utf-8'), document["password"].encode('utf-8'))):      
           return {"data": document , "message" : "Success" ,  "verify" : True}
        else:
            return {"data": document, "message": "Password Mismatch." , "verify" : False}
    else:
        return {"data": None, "message": "User not found." , "verify" : False}



@app.post("/api/send/confirmation/email")
async def sendEmail(user:UserEmail ,background_tasks: BackgroundTasks):
    title = "Confirmation email for varification"
    code = random.randint(1000, 9999)
    async def sendConfirmationThroughemail(email_data:EmailSchema, background_tasks: BackgroundTasks):
        message = MessageSchema(
        subject=email_data.subject,
        recipients=[email_data.email],
        body=email_data.body,
        subtype=MessageType.html
        )
        fm = FastMail(conf)
        background_tasks.add_task(fm.send_message, message)

    def generate_verification_email_body(code: str) -> str:
        return f"""
            <html>
            <body style="font-family: Arial, sans-serif; background-color: #f4f4f7; padding: 20px; color: #333;">
            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width: 600px; margin: auto; background-color: #ffffff; padding: 30px; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.05);">
            <tr>
            <td>
            <h2 style="color: #2089dc; margin-top: 0;">Verify Your Email Address</h2>
            <p style="font-size: 16px;">
            Thank you for signing up for Cloudee. Please use the verification code below to complete your signup:
            </p>
            <div style="font-size: 28px; font-weight: bold; margin: 30px 0; color: #2089dc; text-align: center; letter-spacing: 2px;">
            {code}
            </div>
            <p style="font-size: 14px; color: #666;">
            If you did not request this email, you can safely ignore it.
            </p>
            <br/>
            <p style="font-size: 14px;">Best regards,<br/><strong>Cloudee Team</strong></p>
            </td>
            </tr>
            </table>
            </body>
            </html>
            """

    await sendConfirmationThroughemail(
            EmailSchema(
            email = user.email ,
            subject = title,
            body = generate_verification_email_body(code)
            ),
             background_tasks
            )
    
    return {
        "message" : "Verification email send successfully.",
        "code" : code
    }
 

class UploadPayload(BaseModel):
    email: str
    filename: str
    image_base64: str

@app.post("/base64/image/upload")
async def uploadBase64ImageToCloudinary(payload: UploadPayload):
    collection = db["asset"]
    image_bytes = base64.b64decode(payload.image_base64)
    image_io = io.BytesIO(image_bytes)
    result = cloudinary.uploader.upload(image_io , resource_type="auto" , folder= f"CloudStorageProject/User/Data/{payload.email}/")
    item = {"email" : payload.email,"filename": payload.filename, "url": result.get("secure_url") , "public_id": result.get("public_id") ,  "resource_type": result.get("resource_type") , "created_at" : datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S") }
    dbResponse = await collection.insert_one(item)
    return {"id": str(dbResponse.inserted_id)}
     

@app.post("/api/get/data")
async def fetch_data(email: str = Form(...)):
    collection = db["asset"]
    collection_ = db["user"]

    documents = await collection.find({"email": email}).to_list(length=None)
    user_documents = await collection_.find({"email": email}).to_list(length=1)
    
    for doc in documents:
        doc["_id"] = str(doc["_id"])
        if "created_at" in doc:
            doc["created_at"] = convert_to_local_timezone(doc["created_at"])

    user = None
    if user_documents:
        user = user_documents[0]
        user["_id"] = str(user["_id"])
        remove_key(user, "password")

    return {"data": documents, "user_detail": user}



@app.post("/upload")
async def uploadFileToCloudinary( email: str = Form(...) ,  file: UploadFile = File(...)):
    collection = db["asset"]
    contents = await file.read()
    result = cloudinary.uploader.upload(contents, resource_type="auto" , folder= f"CloudStorageProject/User/Data/{email}/")
    item = {"email" : email,"filename": file.filename, "url": result.get("secure_url") , "public_id": result.get("public_id") ,  "resource_type": result.get("resource_type") , "created_at" : datetime.now().strftime("%Y-%m-%d %H:%M:%S") }
    dbResponse = await collection.insert_one(item)
    return {"id": str(dbResponse.inserted_id)}


@app.delete("/delete")
def delete_file_from_cloudinary(public_id: str = Query(...)):
    collection = db["asset"]
    try:
        result = cloudinary.uploader.destroy(public_id, resource_type="image")
        if result.get("result") == "not_found":
            result = cloudinary.uploader.destroy(public_id, resource_type="raw")
        if result.get("result") == "ok":
            return {"message": "File deleted successfully", "public_id": public_id}
        else:
            raise HTTPException(status_code=404, detail=f"File not found or could not be deleted. Reason: {result.get('result')}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/multiple/upload")
async def upload_multiple_files(
    email: str = Form(...),
    files: List[UploadFile] = File(...)
):
    collection = db["asset"]
    uploaded_files = []

    for file in files:
        contents = await file.read()
        result = cloudinary.uploader.upload(
            contents,
            resource_type="auto",
            folder=f"CloudStorageProject/User/Data/{email}/"
        )
        uploaded_files.append({
            "filename": file.filename,
            "url": result.get("secure_url"),
            "public_id": result.get("public_id"),
            "resource_type": result.get("resource_type")
        })

    return {"uploaded_files": uploaded_files}


@app.delete("/multiple/delete")
def delete_multiple_files(public_ids: List[str] = Body(...)):
    collection = db["asset"]
    deleted = []
    failed = []
    for public_id in public_ids:
        try:
            # First try deleting as image
            result = cloudinary.uploader.destroy(public_id, resource_type="image")
            if result.get("result") == "not_found":
                # If not image, try raw (for PDFs, docs, etc.)
                result = cloudinary.uploader.destroy(public_id, resource_type="raw")

            if result.get("result") == "ok":
                deleted.append(public_id)
            else:
                failed.append({"public_id": public_id, "reason": result.get("result")})
        except Exception as e:
            failed.append({"public_id": public_id, "reason": str(e)})

    return {
        "deleted": deleted,
        "failed": failed
    }

class ItemToDelete(BaseModel):
    public_id: str
    mongo_id: str = Field(..., alias="_id") 


#For image only (main delete function)
@app.post("/api/delete/asset")
async def delete_images(items: List[ItemToDelete] = Body(...)):
    collection = db["asset"]
    deleted_from_cloudinary = []
    deleted_from_db = []

    for item in items:
        try:
            # 🧹 1. Delete from Cloudinary
            cloudinary.uploader.destroy(item.public_id, invalidate=True)
            deleted_from_cloudinary.append(item.public_id)

            # 🧼 2. Delete from MongoDB by _id
            delete_result = await collection.delete_one({"_id": ObjectId(item.mongo_id)})
            if delete_result.deleted_count:
                deleted_from_db.append(item._id)

        except Exception as e:
            print(f"Error deleting {item.public_id}: {e}")
            continue

    return {
        "deleted_from_cloudinary": deleted_from_cloudinary,
        "deleted_from_mongodb": deleted_from_db,
        "total_requested": len(items),
        "success_count": len(deleted_from_db)
    }



