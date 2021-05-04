#!/usr/bin/env python
# coding: utf-8

# 載入相關套件
import datetime
import json
import mysql.connector
import sys
import os
import requests
import time
from flask import Flask, request, abort
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

# Imgur API相關套件
from imgur_python import Imgur

# AzureAI影像服務相關套件
from azure.cognitiveservices.vision.face import FaceClient
from azure.cognitiveservices.vision.face.models import TrainingStatusType
from azure.cognitiveservices.vision.computervision import ComputerVisionClient
from azure.cognitiveservices.vision.computervision.models import OperationStatusCodes
from msrest.authentication import CognitiveServicesCredentials

# LineBot相關套件
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import (MessageEvent, TextMessage, TextSendMessage, ImageMessage, 
    TextSendMessage, FlexSendMessage, ImageSendMessage)
from linebot.models.flex_message import BubbleContainer, ImageComponent
from linebot.models.actions import URIAction


# 讀取相關服務的token資訊
secretFile = json.load(open('secretFile.json', 'r'))

# 讀取LineBot驗證資訊
line_bot_api = LineBotApi(secretFile['line']['channelAccessToken'])
handler = WebhookHandler(secretFile['line']['channelSecret'])

# 建立Imgur API
imgurClient = Imgur(config={
    "client_id": secretFile['imgur']['client_id'],
    "client_secret": secretFile['imgur']['client_secret'],
    "access_token": secretFile['imgur']['access_token'],
    "refresh_token": secretFile['imgur']['refresh_token']
})

# 啟用Azure人臉偵測服務
azureFaceKey = secretFile['azure_face']['key']
azureFaceEndpoint = secretFile['azure_face']['endpoint']
face_client = FaceClient(azureFaceEndpoint, CognitiveServicesCredentials(azureFaceKey))

# 啟用Azure電腦視覺服務
azureCvKey = secretFile['azure_cv']['key']
azureCvEndpoint = secretFile['azure_cv']['endpoint']
cv_client = ComputerVisionClient(azureCvEndpoint, CognitiveServicesCredentials(azureCvKey))

# 設定輸出字體
ttf_path = "./font/TaipeiSansTCBeta-Regular.ttf"
ttf = ImageFont.truetype(ttf_path, 12)


# 建立Flask
app = Flask(__name__)


# linebot接收訊息
@app.route("/", methods=['GET', 'POST'])
def callback():
    
    # 處理GET
    if request.method == 'GET':

        return '''
        <h3>Line機器人-Azure影像辨識服務</h3>
        <span>您好！ 關於此Line機器人的詳細資訊可參考<a href='https://github.com/SuYenTing/linebot-ceb102-heroku'>GitHub專案說明</a></span>
        '''

    # 處理POST
    elif request.method == 'POST':
        
        # get X-Line-Signature header value: 驗證訊息來源
        signature = request.headers['X-Line-Signature']

        # get request body as text: 讀取訊息內容
        body = request.get_data(as_text=True)
        app.logger.info("Request body: " + body)

        # handle webhook body
        try:
            handler.handle(body, signature)
        except InvalidSignatureError:
            print("Invalid signature. Please check your channel access token/channel secret.")
            abort(400)

        return 'OK'


# linebot處理文字訊息
@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):

    replyMsg = '請上傳圖片給我，我能幫您做AI影像處理服務唷!'
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text=replyMsg))


# linebot處理照片訊息
@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):

    # 接收到使用者傳送的原始照片
    message_content = line_bot_api.get_message_content(event.message.id)

    # 照片儲存名稱
    fileName = './image/' + event.message.id + '.jpg'

    # 儲存照片至本地端
    with open(fileName, 'wb')as f:
        for chunk in message_content.iter_content():
            f.write(chunk)

    # 上傳原始照片至imgur
    imgurReply = imgurClient.image_upload(fileName, "title", "description")
    imgurUrl = imgurReply["response"]["data"]["link"]


    # 開始進行Azure圖形辨識服務
    # 設定影像辨識群組
    person_group_id = 'tibame'

    # 執行Azure人臉辨識
    detected_faces = face_client.face.detect_with_url(imgurUrl, detection_model="detection_01", 
        return_recognition_model=True, return_face_landmarks=True)

    # 若有辨識到人臉 則回傳辨識結果給使用者
    if detected_faces:

        # 讀取實際照片
        img = Image.open(fileName)

        # 迴圈偵測到的人臉加入辨識到的資訊
        for face in detected_faces:

            # 每張被偵測到的臉都會有Face ID
            rectangle = face.face_rectangle.as_dict()
            bbox = [
                rectangle["left"],
                rectangle["top"],
                rectangle["left"] + rectangle["width"],
                rectangle["top"] + rectangle["height"],
            ]
            draw = ImageDraw.Draw(img)
            draw.rectangle(bbox, width=3, outline=(255, 0, 0))

            # 人臉偵測名稱
            results = face_client.face.identify([face.face_id], person_group_id)
            result = results[0].as_dict()

            # 如果在資料庫中有找到相像的人 會給予person ID
            # 再拿此person ID去查詢名字
            if result["candidates"]:
                
                person = face_client.person_group_person.get(person_group_id, result["candidates"][0]["person_id"])
                personName = person.name
                confidence = result["candidates"][0]["confidence"]
                imgText = '%s %.2f%%' % (personName, confidence*100)

            else:
                imgText = 'unknown'
                
            # 加入名稱資訊
            draw.rectangle((rectangle["left"], rectangle["top"]-12, rectangle["left"]+12*7 , rectangle["top"]), fill=(255, 0, 0))
            draw.text((rectangle["left"], rectangle["top"]-12), imgText, font=ttf, fill=(255, 255, 255))

        # 儲存加入辨識資訊的照片
        saveFileName = './image/' + event.message.id + '_face.jpg'
        img.save(saveFileName)

        # 將加入辨識資訊的照片傳至imgur
        imgurReply = imgurClient.image_upload(saveFileName, "title", "description")
        imgFaceUrl = imgurReply["response"]["data"]["link"]

        # linebot回傳照片
        line_bot_api.reply_message(
            event.reply_token,
            ImageSendMessage(original_content_url=imgFaceUrl, preview_image_url=imgFaceUrl))

        # 刪除加入辨識資訊的照片
        os.remove(saveFileName)


    # 開始進行OCR字元辨識
    ocr_results = cv_client.read(imgurUrl, raw=True)
    operation_location_remote = ocr_results.headers["Operation-Location"]
    operation_id = operation_location_remote.split("/")[-1]

    # 偵測OCR字元辨識是否已執行完畢
    status = ["notStarted", "running"]
    while True:
        get_handw_text_results = cv_client.get_read_result(operation_id)
        if get_handw_text_results.status not in status:
            break
        # time.sleep(1)

    ocrText = []  # 存放OCR的辨識文字
    if get_handw_text_results.status == OperationStatusCodes.succeeded:

        # 讀取實際照片
        img = Image.open(fileName)
        draw = ImageDraw.Draw(img)

        res = get_handw_text_results.analyze_result.read_results
        for text_result in res:
            for line in text_result.lines:
                ocrText.append(line.text)
                bounding_box = line.bounding_box
                bounding_box += bounding_box[:2]
                draw.line(line.bounding_box, fill=(255, 0, 0), width=2)

    # 若有辨識出OCR字元 則回傳結果給使用者
    if ocrText:

        # 組合ocr字元
        ocrText = '\n'.join(ocrText)

        # 儲存處理好的照片
        saveFileName = './image/' + event.message.id + '_ocr.jpg'
        img.save(saveFileName)

        # 將處理好的照片傳至imgur
        img = imgurClient.image_upload(saveFileName, "title", "description")
        imgOcrUrl = img["response"]["data"]["link"]

        # 讀取flex message格式
        bubble = json.load(open("flexMsgTemplate.json", 'r'))
        bubble["hero"]["url"] = imgOcrUrl
        bubble["hero"]["action"]["uri"] = imgOcrUrl
        bubble["body"]["contents"][0]["text"] = ocrText

        # linebot回傳flex message
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="Report", contents=bubble))

        # 刪除處理好的照片
        os.remove(saveFileName)


    # 開始進行圖片描述
    description_results = cv_client.describe_image(imgurUrl)
    describleText = ""
    for caption in description_results.captions:
    
        describleText += "'{}' with confidence {:.2f}% \n".format(caption.text, caption.confidence * 100)
    
    if describleText:
    
        # 讀取flex message格式
        bubble = json.load(open("flexMsgTemplate.json", 'r'))
        bubble["hero"]["url"] = imgurUrl
        bubble["hero"]["action"]["uri"] = imgurUrl
        bubble["body"]["contents"][0]["text"] = describleText

        # linebot回傳flex message
        line_bot_api.reply_message(event.reply_token, FlexSendMessage(alt_text="Report", contents=bubble))

    # 刪除本地端照片
    os.remove(fileName)


# 開始運作Flask
if __name__ == "__main__":

    try:
        app.run(host='0.0.0.0')

    except Exception as error:
        print(error)

    finally:
        print("Done")
