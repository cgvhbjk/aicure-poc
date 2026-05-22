import sqlite3
c = sqlite3.connect('data/aicure.db')
c.execute('DELETE FROM trial_news_links')
c.execute('DELETE FROM news_items')
c.execute('UPDATE trials SET has_news=0')
c.commit()
c.close()
print('Cleared.')
