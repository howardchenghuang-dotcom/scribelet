'use strict';
const CACHE='scribelet-shell-v2';
const SHELL=['/','/app.js','/style.css','/manifest.webmanifest'];
self.addEventListener('install',event=>event.waitUntil(caches.open(CACHE).then(cache=>cache.addAll(SHELL))));
self.addEventListener('activate',event=>event.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k.startsWith('scribelet-shell-')&&k!==CACHE).map(k=>caches.delete(k))))));
self.addEventListener('fetch',event=>{
  const url=new URL(event.request.url);
  // Never cache credentials, API responses, private notes or recordings.
  if(event.request.method!=='GET'||url.origin!==self.location.origin||!SHELL.includes(url.pathname))return;
  event.respondWith(fetch(event.request).catch(()=>caches.match(url.pathname)));
});
