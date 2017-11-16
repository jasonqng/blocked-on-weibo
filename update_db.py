import sqlite3
import os, sys


def find_db():
    print("Finding database files...")
    files = list(os.walk('.'))[0][2]
    db_files = [file for file in files if ".db" in file or ".sqlite" in file]
    if len(db_files)>0:
        if len(db_files) == 1:
            user_input = raw_input("Is database file %s/%s? (y/N) " %(os.getcwd(), db_files[0]))
            if user_input.lower() == 'y':
                migrate_db(db_files[0])
            else:
                break_script()
        else:
            print("%d database files found in dir" %(len(db_files)))
            for i in range(len(db_files)):
                print("%d. %s" %(i+1, db_files[i]))
            print("**************")
            user_input = raw_input("Which db file to upgrade? (Enter NUMBER) ")
            if user_input.isdigit() and 0 < int(user_input) < len(db_files):
                migrate_db(db_files[int(user_input) - 1])
            else:
                print("Invalid input.")
                break_script()
    else:
        break_script()


def migrate_db(db_location):
    conn = sqlite3.connect(db_location)
    c = conn.cursor()
    c.execute('ALTER TABLE results RENAME TO resultsOld')
    c.execute('''CREATE TABLE results (id INT, date DATE, datetime_logged DATETIME, test_number INT, keyword string, 
        censored bool, no_results bool, reset bool, is_canonical bool, result string, source string, orig_keyword string, 
        num_results INT, notes string, PRIMARY KEY(date,source,test_number,keyword,orig_keyword))''')
    c.execute('''INSERT INTO results (id, date, datetime_logged, test_number, keyword, censored, no_results, reset,
        result, source, num_results, notes)
                SELECT id, date, datetime_logged, test_number, keyword, censored, no_results, reset, result, source,
                  num_results, notes FROM resultsOld
    ''')
    c.execute('DROP TABLE resultsOld')
    conn.commit()
    conn.close()
    print("Updates completed successfully")
    sys.exit()


def break_script():
    print("Please place this .py script in the same directory as your database file, then re-run. No changes were made")
    sys.exit()


if __name__ == "__main__":
    find_db()