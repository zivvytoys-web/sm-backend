import pymysql

try:
    conn = pymysql.connect(host='127.0.0.1', user='root', password='', port=3307)
    with conn.cursor() as cursor:
        cursor.execute("CREATE DATABASE IF NOT EXISTS social_ai_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
        cursor.execute("SHOW DATABASES;")
        dbs = [row[0] for row in cursor.fetchall()]
        print("Connected to MySQL successfully! Databases:", dbs)
    conn.close()
except Exception as e:
    print("Connection error:", e)
