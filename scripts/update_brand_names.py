import pymysql

def update_db():
    conn = pymysql.connect(host='127.0.0.1', user='root', password='Gokul123', port=3306, database='social_ai')
    with conn.cursor() as cur:
        cur.execute("UPDATE brand_profiles SET name='aminoid', tagline='Own Your Style with aminoid.', industry='AI & Fashion Studio' WHERE id > 0;")
        cur.execute("UPDATE organizations SET name='aminoid', slug=CONCAT('aminoid-', id) WHERE id > 0;")
        cur.execute("UPDATE users SET full_name='aminoid Admin' WHERE email LIKE '%dollu%' OR email LIKE '%admin%' OR id = 2;")
        conn.commit()
        print("MySQL brand profiles, organizations, and users successfully updated to 'aminoid'!")
    conn.close()

if __name__ == '__main__':
    update_db()
