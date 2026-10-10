//! LRU cache of serialized access-network GeoJSON responses.

use std::num::NonZeroUsize;
use std::sync::Arc;

use lru::LruCache;
use peaky_peaks::{NetworkLayer, NetworkViewCacheKey, ProjectRouting};

const CACHE_CAP: usize = 32;

#[derive(Clone, Copy, Eq, PartialEq, Hash, Debug)]
enum CacheKind {
    Layer(NetworkLayer),
    DriveBoth,
    AllInventory,
}

#[derive(Clone, Copy, Eq, PartialEq, Hash, Debug)]
struct CacheKey {
    kind: CacheKind,
    view: NetworkViewCacheKey,
}

pub struct NetworkGeoByteCache {
    routing_id: usize,
    lru: LruCache<CacheKey, Arc<Vec<u8>>>,
}

impl NetworkGeoByteCache {
    pub fn new() -> Self {
        Self {
            routing_id: 0,
            lru: LruCache::new(NonZeroUsize::new(CACHE_CAP).unwrap()),
        }
    }

    fn touch_routing(&mut self, routing: &ProjectRouting) {
        let id = routing as *const ProjectRouting as usize;
        if self.routing_id != id {
            self.routing_id = id;
            self.lru.clear();
        }
    }

    pub fn get_layer(
        &mut self,
        routing: &ProjectRouting,
        layer: NetworkLayer,
        view: NetworkViewCacheKey,
    ) -> Option<Arc<Vec<u8>>> {
        self.touch_routing(routing);
        let key = CacheKey {
            kind: CacheKind::Layer(layer),
            view,
        };
        self.lru.get(&key).cloned()
    }

    pub fn put_layer(
        &mut self,
        routing: &ProjectRouting,
        layer: NetworkLayer,
        view: NetworkViewCacheKey,
        bytes: Vec<u8>,
    ) {
        self.touch_routing(routing);
        let key = CacheKey {
            kind: CacheKind::Layer(layer),
            view,
        };
        self.lru.put(key, Arc::new(bytes));
    }

    pub fn get_drive(&mut self, routing: &ProjectRouting, view: NetworkViewCacheKey) -> Option<Arc<Vec<u8>>> {
        self.touch_routing(routing);
        let key = CacheKey {
            kind: CacheKind::DriveBoth,
            view,
        };
        self.lru.get(&key).cloned()
    }

    pub fn put_drive(&mut self, routing: &ProjectRouting, view: NetworkViewCacheKey, bytes: Vec<u8>) {
        self.touch_routing(routing);
        let key = CacheKey {
            kind: CacheKind::DriveBoth,
            view,
        };
        self.lru.put(key, Arc::new(bytes));
    }

    pub fn get_all(&mut self, routing: &ProjectRouting, view: NetworkViewCacheKey) -> Option<Arc<Vec<u8>>> {
        self.touch_routing(routing);
        let key = CacheKey {
            kind: CacheKind::AllInventory,
            view,
        };
        self.lru.get(&key).cloned()
    }

    pub fn put_all(&mut self, routing: &ProjectRouting, view: NetworkViewCacheKey, bytes: Vec<u8>) {
        self.touch_routing(routing);
        let key = CacheKey {
            kind: CacheKind::AllInventory,
            view,
        };
        self.lru.put(key, Arc::new(bytes));
    }

    pub fn clear(&mut self) {
        self.routing_id = 0;
        self.lru.clear();
    }
}

impl Default for NetworkGeoByteCache {
    fn default() -> Self {
        Self::new()
    }
}
