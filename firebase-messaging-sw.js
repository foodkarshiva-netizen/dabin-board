/* firebase-messaging-sw.js — 다빈보드 앱 푸시(백그라운드 수신) · 2026-09-10
   앱이 꺼져 있거나 다른 탭을 보고 있을 때 오는 푸시를 이 워커가 받아 알림으로 띄운다. */
importScripts('https://www.gstatic.com/firebasejs/10.12.2/firebase-app-compat.js');
importScripts('https://www.gstatic.com/firebasejs/10.12.2/firebase-messaging-compat.js');

firebase.initializeApp({apiKey:"AIzaSyDV_8R2w2YjINtcmTsxpG0AsLEpaTMRVkg",authDomain:"dabin-board.firebaseapp.com",projectId:"dabin-board",storageBucket:"dabin-board.firebasestorage.app",messagingSenderId:"161372753030",appId:"1:161372753030:web:30c4c881a5fb360bfc5507"});
var messaging = firebase.messaging();

messaging.onBackgroundMessage(function(payload){
  var d = payload.data || {};
  var title = d.title || '다빈 보드';
  self.registration.showNotification(title, {
    body: d.body || '',
    icon: './icon-192.png',
    badge: './icon-192.png',
    tag: d.tag || 'dabin',
    renotify: true,
    data: { url: d.url || './?v=app' }
  });
});

/* 알림을 누르면 이미 열려 있는 보드 탭으로 이동, 없으면 새로 연다 */
self.addEventListener('notificationclick', function(e){
  e.notification.close();
  var url = (e.notification.data && e.notification.data.url) || './?v=app';
  e.waitUntil(clients.matchAll({type:'window', includeUncontrolled:true}).then(function(list){
    for (var i=0;i<list.length;i++){
      if (list[i].url.indexOf('dabin-board') >= 0 && 'focus' in list[i]) {
        if (list[i].navigate) { try { list[i].navigate(url) } catch(x){} }
        return list[i].focus();
      }
    }
    if (clients.openWindow) return clients.openWindow(url);
  }));
});
