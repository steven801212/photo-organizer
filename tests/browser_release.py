"""Real Chromium smoke checks against a temporary Organizer HTTP service."""
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from PIL import Image
from playwright.sync_api import sync_playwright, expect
from photo_organizer.auth import AuthStore
from photo_organizer.database import Database
from photo_organizer import __version__
import photo_organizer.service as service


def seed(username, count, color):
    config=service.tenant_config(username); config.ensure_directories()
    db=Database(config.db_path)
    try:
        album=db.create_album('Review album','', 'now')
        ids=[]
        with db.transaction():
            for i in range(count):
                path=config.jpg_dir/f'{username}-{i:03d}.jpg'
                Image.new('RGB',(240,160),color).save(path)
                date={0:'2012-01-01T12:00:00',1:'2026-09-08T12:00:00',2:'2024-01-02T12:00:00',3:'2026-01-01T12:00:00',4:None}.get(i,'2023-02-04T12:00:00')
                photo=db.add_photo(sha256=f'{i+1:064x}',original_path=str(path),current_path=str(path),capture_date=date,file_type='JPG',extension='.jpg',file_size=path.stat().st_size,imported_at='2026-09-08',status='ACTIVE')
                db.connection.execute('UPDATE photos SET gps_latitude=25,gps_longitude=121 WHERE id=?',(photo,));ids.append(photo)
        db.add_to_album(album,ids,'now')
        db.set_setting('auto_scan','True','now')  # Backward compatibility with Beta values.
    finally: db.close()


def main():
    with TemporaryDirectory(prefix='photo-browser-review-') as temp:
        root=Path(temp)
        os.environ.update(PHOTO_INBOX_ROOT=str(root/'inbox'),PHOTO_LIBRARY_ROOT=str(root/'library'),PHOTO_BACKUP_ROOT=str(root/'backup'),PHOTO_REJECT_ROOT=str(root/'rejected'))
        service.SYSTEM_DATA=root/'data';service.STATES.clear();service.AUTH=AuthStore(root/'data'/'system')
        service.AUTH.bootstrap('admin','1');service.AUTH.create_user('bob','1')
        seed('admin',65,'red');seed('bob',1,'blue')
        server=ThreadingHTTPServer(('127.0.0.1',0),service.Handler);Thread(target=server.serve_forever,daemon=True).start()
        errors=[]
        try:
            with patch('photo_organizer.thumbnails._preview_bytes',return_value=None),patch('photo_organizer.service.exif_details',return_value={'Model':'Review camera'}),patch.object(service.Handler,'log_message'),sync_playwright() as p:
                browser=p.chromium.launch(headless=True,channel=os.environ.get('PHOTO_TEST_BROWSER_CHANNEL') or None)
                page=browser.new_page(viewport={'width':1440,'height':1000})
                page.on('pageerror',lambda error:errors.append(str(error)))
                # Test map behavior locally without sending synthetic locations to a tile service.
                page.route('https://tile.openstreetmap.org/**',lambda route:route.abort())
                page.goto(f'http://127.0.0.1:{server.server_port}')
                def login(username):
                    page.fill('#username',username);page.fill('#password','1');page.click('#loginForm button')
                    expect(page.locator('#login')).to_have_class('login hidden')
                    expect(page.locator('#health')).to_contain_text(username)
                login('admin')
                expect(page.locator('#appVersion')).to_have_text('v'+__version__)
                Path('artifacts').mkdir(exist_ok=True)
                widths=[1280,1920,2560,3840]
                heights=[800,1080,1440,2160]
                for width,height in zip(widths,heights):
                    page.set_viewport_size({'width':width,'height':height})
                    # Main content must remain top aligned and use available width.
                    box=page.locator('main').bounding_box()
                    heading=page.locator('.toolbar').bounding_box()
                    assert box['y']==0 and 15<=heading['y']<=48,(width,box,heading)
                    assert width-50<=heading['x']+heading['width']<=width-15,(width,heading)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width
                page.screenshot(path='artifacts/release-overview-4k.png')
                page.click('[data-view="settings"]');expect(page.locator('#autoScan')).to_be_checked();expect(page.locator('#autoPurge')).not_to_be_checked()
                page.click('[data-view="gallery"]');expect(page.locator('#galleryGrid .photo-card')).to_have_count(60)
                expect(page.locator('#galleryFolders details')).to_have_count(5)
                assert page.locator('#galleryFolders summary').all_text_contents()==['2026 2','2024 1','2023 60','2012 1','日期未知 1']
                expect(page.locator('#galleryFolders details[open]')).to_have_count(0)
                expect(page.locator('#galleryGrid .photo-card').first).to_contain_text('admin-001.jpg')
                first_year=page.locator('#galleryFolders details').first
                first_year.locator('summary').focus();page.keyboard.press('Enter')
                expect(page.locator('#galleryFolders details[open]')).to_have_count(1)
                assert first_year.locator('.folder').all_text_contents()==['09-081','01-011']
                first_year.locator('.folder').last.click()
                expect(page.locator('#galleryGrid .photo-card')).to_have_count(1)
                expect(page.locator('#galleryGrid')).to_contain_text('admin-003.jpg')
                first_year.locator('summary').click()
                expect(page.locator('#galleryFolders details[open]')).to_have_count(0)
                page.locator('#galleryFolders .all').click()
                expect(page.locator('#galleryGrid .photo-card')).to_have_count(60)
                columns=[]
                for width,height in zip(widths,heights):
                    page.set_viewport_size({'width':width,'height':height})
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'),width
                    columns.append(page.locator('#galleryGrid').evaluate('el=>getComputedStyle(el).gridTemplateColumns.split(" ").length'))
                assert all(a<b for a,b in zip(columns,columns[1:])),columns
                page.screenshot(path='artifacts/release-gallery-4k.png')
                page.set_viewport_size({'width':1920,'height':1080})
                page.screenshot(path='artifacts/release-gallery-desktop.png')
                page.click('#galleryMore');expect(page.locator('#galleryGrid .photo-card')).to_have_count(65)
                expect(page.locator('#galleryMore')).to_be_hidden()
                page.locator('#galleryGrid .photo-card').first.click();expect(page.locator('#lightbox')).to_be_visible()
                expect(page.locator('#exifDetails')).to_contain_text('Review camera')
                old_src=page.locator('#lightboxImage').get_attribute('src')
                with page.expect_response(lambda r:'/api/preview/' in r.url) as response_info:
                    page.keyboard.press('ArrowRight')
                assert 'no-store' in response_info.value.headers['cache-control']
                page.click('#lightboxClose')
                page.click('[data-view="albums"]');page.click('[data-album]')
                expect(page.locator('#albumGrid .photo-card')).to_have_count(60)
                page.locator('#albums > .load-more').click();expect(page.locator('#albumGrid .photo-card')).to_have_count(65)
                page.click('[data-view="map"]');expect(page.locator('.photo-map-marker')).to_have_count(1)
                page.locator('.photo-map-marker').click();expect(page.locator('.map-photo-popup > div button')).to_have_count(30)
                page.locator('.map-photo-popup footer button').last.click();expect(page.locator('.map-photo-popup footer small')).to_have_text('2 / 3')
                page.locator('.map-photo-popup footer button').last.click();expect(page.locator('.map-photo-popup > div button')).to_have_count(5)
                page.locator('.map-photo-popup > div button').last.click();expect(page.locator('#lightbox')).to_be_visible()
                page.click('#lightboxClose');page.click('#logout')
                expect(page.locator('#galleryGrid .photo-card')).to_have_count(0)
                assert page.locator('#lightboxImage').get_attribute('src') is None
                login('bob');page.click('[data-view="gallery"]');expect(page.locator('#galleryGrid .photo-card')).to_have_count(1)
                expect(page.locator('#galleryGrid')).to_contain_text('bob-000.jpg')
                page.locator('#galleryGrid .photo-card').click();expect(page.locator('#lightboxName')).to_have_text('bob-000.jpg')
                assert page.locator('#lightboxImage').get_attribute('src') != old_src
                page.click('#lightboxClose')
                Path('artifacts').mkdir(exist_ok=True)
                page.screenshot(path='artifacts/release-browser.png',full_page=True)
                browser.close()
            assert not errors, errors
            print(json.dumps({'browser':os.environ.get('PHOTO_TEST_BROWSER_CHANNEL','Chromium'),'result':'PASS','resolutions':list(zip(widths,heights)),'gallery_columns':columns,'checks':['newest-first years/dates/photos','unknown dates last','collapsed folders','keyboard expand and date selection','fluid top-aligned layout','login','legacy settings','gallery pagination','album pagination','map group pagination','lightbox EXIF','keyboard navigation','private media cache headers','logout clearing','account switch']},ensure_ascii=False))
        finally:
            server.shutdown();server.server_close();service.AUTH.close();service.STATES.clear()


if __name__=='__main__': main()
