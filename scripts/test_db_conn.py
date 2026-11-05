import pymysql

passwords = ['', 'password', 'root', 'admin', '123456', '12345678', 'MySQL80', 'root123', 'gokul', 'Gokul@123', 'root@123']
connected = False
for p in passwords:
    try:
        conn = pymysql.connect(host='127.0.0.1', user='root', password=p, port=3306)
        print(f"SUCCESS with password: '{p}'")
        with conn.cursor() as cursor:
            cursor.execute("CREATE DATABASE IF NOT EXISTS social_ai_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
            cursor.execute("SHOW DATABASES;")
            print("Databases:", [row[0] for row in cursor.fetchall()])
        conn.close()
        connected = True
        break
    except Exception as e:
        print(f"Failed with '{p}': {e}")
