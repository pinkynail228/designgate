;; This Source Code Form is subject to the terms of the Mozilla Public
;; License, v. 2.0. If a copy of the MPL was not distributed with this
;; file, You can obtain one at http://mozilla.org/MPL/2.0/.
;;
;; Copyright (c) KALEIDOS INC Sucursal en España SL

(ns app.main.data.workspace.component-uses
  "Read only helpers to count how many instances of a component live inside
  a file. Nothing here touches the store or mutates the file data."
  (:require
   [app.common.transit :as t]
   [app.common.types.component :as ctc]))

(defn fully-loaded?
  "Check that every page of the file data is loaded.

  When the `pointer-map` feature is active, the bundle keeps the pages as
  lazy pointers that the frontend replaces by the real page while resolving
  the file (see `app.main.features.pointer-map`). A page stored as a
  pointer, missing, or without objects is not loaded, so a count made from
  the file data is a lower bound of the real number, not the number itself."
  [file-data]
  (let [pages     (get file-data :pages-index)
        page-ids  (or (not-empty (get file-data :pages))
                      (keys pages))]
    (and (seq page-ids)
         (every? (fn [page-id]
                   (let [page (get pages page-id)]
                     (and (some? page)
                          (not (t/pointer? page))
                          (some? (:objects page)))))
                 page-ids))))

(defn count-uses
  "Return a map of `{component-id count}` with the number of instances of the
  components of the given library file that live on the file data.

  An instance is every shape that points to a component of that library, so
  the `:component-file` plus `:component-id` pair is the identity of the
  component (see `app.common.types.component/instance-of?`). Nested instances
  count too, as Figma does. The main component is left out
  (`:main-instance`), because it is the definition, not a use.

  Return `nil` when the file data is not fully loaded (see `fully-loaded?`):
  `nil` means the number is unknown, never zero."
  [file-data library-id]
  (when (fully-loaded? file-data)
    (reduce-kv
     (fn [result _ page]
       (reduce (fn [result shape]
                 (let [component-id (:component-id shape)]
                   (if (and (ctc/instance-of? shape library-id component-id)
                            (not (ctc/main-instance? shape)))
                     (update result component-id (fnil inc 0))
                     result)))
               result
               (vals (:objects page))))
     {}
     (:pages-index file-data))))
