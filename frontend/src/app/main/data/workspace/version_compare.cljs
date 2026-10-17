;; This Source Code Form is subject to the terms of the Mozilla Public
;; License, v. 2.0. If a copy of the MPL was not distributed with this
;; file, You can obtain one at http://mozilla.org/MPL/2.0/.
;;
;; Copyright (c) KALEIDOS INC Sucursal en España SL

(ns app.main.data.workspace.version-compare
  "Read-only helpers and events for comparing a saved version with the
  current file content. Nothing here writes the file data, the versions
  list or the library: the result is stored only in
  `[:workspace-versions :compare]`."
  (:require
   [app.common.logging :as log]
   [app.common.types.tokens-lib :as ctob]
   [app.common.uuid :as uuid]
   [app.main.data.helpers :as dsh]
   [app.main.features :as features]
   [app.main.features.pointer-map :as fpmap]
   [app.main.repo :as rp]
   [beicon.v2.core :as rx]
   [clojure.set :as set]
   [clojure.walk :as walk]
   [potok.v2.core :as ptk]))

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;; PURE DIFF
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

(def ignored-keys
  "Keys removed from an asset value before comparing it. `:modified-at`
  changes on every edition (and on every snapshot) and does not describe
  a real asset change; components ignore nothing else."
  #{:modified-at})

(defn- clean
  "Recursively removes `ignored-keys` from `form`, so a change that only
  touches volatile keys is not reported as a change."
  [form]
  (walk/postwalk
   (fn [node]
     (if (map? node)
       (apply dissoc node ignored-keys)
       node))
   form))

(defn- asset-entry
  [asset]
  {:id (:id asset)
   :name (or (:name asset) "")
   :path (or (:path asset) "")})

(defn- token-entry
  [token]
  {:id (:id token)
   :name (or (:name token) "")
   :path ""})

(defn- sort-entries
  [entries]
  (->> entries
       (sort-by (juxt :path :name))
       (vec)))

(defn- deleted-component?
  [asset]
  (true? (:deleted asset)))

(defn- diff-coll
  "Generic diff of two `id -> asset` maps. `deleted?` tells whether an
  asset is removed in the new data (components keep a `:deleted` flag
  instead of being deleted)."
  [old-assets new-assets deleted?]
  (let [old-assets (or old-assets {})
        new-assets (or new-assets {})
        alive      (into {} (remove (fn [[_ asset]] (deleted? asset))) new-assets)
        gone?      (fn [id]
                     (let [asset (get new-assets id)]
                       (or (nil? asset) (deleted? asset))))
        added      (->> (keys alive)
                        (remove #(contains? old-assets %))
                        (map #(asset-entry (get alive %))))
        changed    (->> (keys alive)
                        (filter #(contains? old-assets %))
                        (filter #(not= (clean (get old-assets %))
                                       (clean (get alive %))))
                        (map #(asset-entry (get alive %))))
        removed    (->> (keys old-assets)
                        ;; an asset already deleted in the old data is not a
                        ;; change: it was removed before this version.
                        (remove #(deleted? (get old-assets %)))
                        (filter gone?)
                        (map #(asset-entry (get old-assets %))))]
    {:added   (sort-entries added)
     :changed (sort-entries changed)
     :removed (sort-entries removed)}))

(defn- tokens-map
  "All tokens of the library indexed by token name, using the public
  TokensLib API."
  [tokens-lib]
  (if (ctob/tokens-lib? tokens-lib)
    (ctob/get-all-tokens-map tokens-lib)
    {}))

(defn- token-changed?
  [old-token new-token]
  (or (not= (:type old-token) (:type new-token))
      (not= (:value old-token) (:value new-token))))

(defn- diff-tokens
  "Diff of two TokensLib instances by token name, across all sets."
  [old-lib new-lib]
  (let [old-tokens (tokens-map old-lib)
        new-tokens (tokens-map new-lib)
        old-names  (set (keys old-tokens))
        new-names  (set (keys new-tokens))]
    {:added   (->> (set/difference new-names old-names)
                   (map #(get new-tokens %))
                   (map token-entry)
                   (sort-entries))
     :changed (->> (set/intersection old-names new-names)
                   (filter #(token-changed? (get old-tokens %) (get new-tokens %)))
                   (map #(get new-tokens %))
                   (map token-entry)
                   (sort-entries))
     :removed (->> (set/difference old-names new-names)
                   (map #(get old-tokens %))
                   (map token-entry)
                   (sort-entries))}))

(defn- page-shapes
  "Shapes of a page indexed by id. The root frame (`uuid/zero`) is a
  structural node and is never reported as a layer."
  [page]
  (into {} (remove (fn [[id _]] (= id uuid/zero))) (or (:objects page) {})))

(defn- layer-entry
  [shape]
  {:id (:id shape)
   :name (or (:name shape) "")
   :type (:type shape)})

(defn- sort-layers
  [entries]
  (->> entries
       (sort-by (juxt :name #(str (:id %))))
       (vec)))

(defn- diff-page-shapes
  "Diff of two page `:objects` maps, as the `:added` `:changed` and
  `:removed` lists of `{:id :name :type}` entries sorted by name."
  [old-page new-page]
  (let [old-shapes (page-shapes old-page)
        new-shapes (page-shapes new-page)
        added      (->> (keys new-shapes)
                        (remove #(contains? old-shapes %))
                        (map #(layer-entry (get new-shapes %))))
        changed    (->> (keys new-shapes)
                        (filter #(contains? old-shapes %))
                        (filter #(not= (clean (get old-shapes %))
                                       (clean (get new-shapes %))))
                        (map #(layer-entry (get new-shapes %))))
        removed    (->> (keys old-shapes)
                        (remove #(contains? new-shapes %))
                        (map #(layer-entry (get old-shapes %))))]
    {:added   (sort-layers added)
     :changed (sort-layers changed)
     :removed (sort-layers removed)}))

(defn- changed-page?
  "True when a page kept in both datas holds a reportable change: a new
  name or at least one layer added, changed or removed."
  [old-page new-page diff]
  (or (not= (:name old-page) (:name new-page))
      (seq (:added diff))
      (seq (:changed diff))
      (seq (:removed diff))))

(defn diff-pages
  "Pure diff of the page contents of two file `:data` maps. `old-data` is
  the saved version data and `new-data` the current file data. Returns a
  vector in current page order (then the removed pages) of
  `{:id :name :status :added :changed :removed}` entries, where `:status`
  is `:added`, `:changed` or `:removed` and every layer list holds
  `{:id :name :type}` entries sorted by name. Pages without change and
  with the same name are left out; the root frame is never reported."
  [old-data new-data]
  (let [old-data  (or old-data {})
        new-data  (or new-data {})
        old-index (or (:pages-index old-data) {})
        new-index (or (:pages-index new-data) {})
        old-order (vec (:pages old-data))
        new-order (vec (:pages new-data))
        new-ids   (set new-order)

        current
        (for [page-id new-order
              :let [page (get new-index page-id)]
              :when page]
          (if (contains? old-index page-id)
            (let [old-page (get old-index page-id)
                  diff     (diff-page-shapes old-page page)]
              (when (changed-page? old-page page diff)
                (assoc diff
                       :id page-id
                       :name (or (:name page) "")
                       :status :changed)))
            (assoc (diff-page-shapes nil page)
                   :id page-id
                   :name (or (:name page) "")
                   :status :added)))

        removed
        (for [page-id old-order
              :when (not (contains? new-ids page-id))
              :let [page (get old-index page-id)]
              :when page]
          (assoc (diff-page-shapes page nil)
                 :id page-id
                 :name (or (:name page) "")
                 :status :removed))]

    (->> (concat current removed)
         (remove nil?)
         (vec))))

(defn diff-assets
  "Compute the asset differences between two file `:data` maps. `old-data`
  is the saved version data and `new-data` the current file data. Returns
  a map with one entry per asset kind, each one holding the `:added`,
  `:changed` and `:removed` lists of `{:id :name :path}` entries sorted
  by path and name, plus a `:pages` entry with the per page layer diff
  (see `diff-pages`)."
  [old-data new-data]
  (let [old-data (or old-data {})
        new-data (or new-data {})]
    {:components   (diff-coll (:components old-data)
                              (:components new-data)
                              deleted-component?)
     :colors       (diff-coll (:colors old-data)
                              (:colors new-data)
                              (constantly false))
     :typographies (diff-coll (:typographies old-data)
                              (:typographies new-data)
                              (constantly false))
     :tokens       (diff-tokens (:tokens-lib old-data)
                                (:tokens-lib new-data))
     :pages        (diff-pages old-data new-data)}))

;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;
;; EVENTS
;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;;

(defn- start-compare
  "Marks a compare request as in flight. `token` identifies the request so
  the answers of superseded requests can be dropped."
  [token]
  (ptk/reify ::start-compare
    ptk/UpdateEvent
    (update [_ state]
      (update state :workspace-versions assoc :compare
              {:status :loading :token token}))))

(defn clear-compare
  "Removes the compare result from the app state. Used by the compare
  panel close button. A request still in flight is not cancelled but its
  answer no longer matches the compare state, so it gets dropped."
  []
  (ptk/reify ::clear-compare
    ptk/UpdateEvent
    (update [_ state]
      (update state :workspace-versions dissoc :compare))))

(defn- request-in-flight?
  "True when the compare state still is the request identified by `token`.
  It is false once the panel is closed or a newer request has started."
  [state token]
  (let [compare (get-in state [:workspace-versions :compare])]
    (and (= :loading (:status compare))
         (= token (:token compare)))))

(defn- store-compare-diff
  "Stores ONLY the diff between `version-data` and the current file data.
  The answer is dropped when `token` is no longer the request in flight
  (closed panel or newer request) and the panel is closed when the current
  file is not `file-id` anymore: the snapshot then belongs to another file
  and there is nothing safe to compare it with."
  [token file-id version-data]
  (ptk/reify ::store-compare-diff
    ptk/UpdateEvent
    (update [_ state]
      (cond
        (not (request-in-flight? state token))
        state

        (not= file-id (:current-file-id state))
        (update state :workspace-versions dissoc :compare)

        :else
        (update state :workspace-versions assoc :compare
                {:status :loaded
                 :data (diff-assets version-data
                                    (dsh/lookup-file-data state))})))))

(defn- fail-compare
  "Marks the request identified by `token` as failed. A late failure of a
  superseded or closed request is ignored."
  [token]
  (ptk/reify ::fail-compare
    ptk/UpdateEvent
    (update [_ state]
      (if (request-in-flight? state token)
        (update state :workspace-versions assoc :compare {:status :error})
        state))))

(defn fetch-and-compare
  "Read-only event: loads the given saved version and stores in the state
  the difference between its assets and the current file assets. It does
  not modify the file data, does not persist anything and does not enter
  the preview mode. An answer arriving after the panel was closed or after
  another request started is dropped."
  [id]
  (assert (uuid? id) "expected valid uuid for `id`")

  (ptk/reify ::fetch-and-compare
    ptk/WatchEvent
    (watch [_ state _]
      (let [file-id  (:current-file-id state)
            team-id  (:current-team-id state)
            features (features/get-enabled-features state team-id)
            ;; identifies this request so a late answer can be recognised
            ;; as stale (see `store-compare-diff` and `fail-compare`).
            token    (uuid/next)]
        (rx/concat
         (rx/of (start-compare token))

         (->> (rp/cmd! :get-file-snapshot
                       {:file-id file-id
                        :id id
                        :features features})
              ;; The snapshot data may still contain pointer-map fragments
              ;; (pages and the components map); resolve them the same way
              ;; `resolve-file` does for a regular file.
              (rx/mapcat fpmap/resolve-file)
              (rx/map :data)
              (rx/mapcat (fn [data] (rx/of (store-compare-diff token file-id data))))

              (rx/catch (fn [cause]
                          (log/error :hint "failed to fetch version for comparison"
                                     :cause cause
                                     :file-id file-id
                                     :version-id id)
                          (rx/of (fail-compare token))))))))))
