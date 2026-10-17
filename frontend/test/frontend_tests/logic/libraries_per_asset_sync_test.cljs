;; This Source Code Form is subject to the terms of the Mozilla Public
;; License, v. 2.0. If a copy of the MPL was not distributed with this
;; file, You can obtain one at http://mozilla.org/MPL/2.0/.
;;
;; Copyright (c) KALEIDOS INC Sucursal en España SL

(ns frontend-tests.logic.libraries-per-asset-sync-test
  "Updating a single asset from the libraries dialog must not mark the whole
  library as synced. When it does, the assets that are still pending vanish
  from the updates list and from the notification bubble."
  (:require
   [app.common.uuid :as uuid]
   [app.main.data.workspace.libraries :as dwl]
   [cljs.test :as t :include-macros true]
   [potok.v2.core :as ptk]))

(def ^:private file-id (uuid/next))
(def ^:private library-id (uuid/next))

(defn- state-with-library
  []
  {:current-file-id file-id
   :files {library-id {:id library-id}}})

(t/deftest per-asset-sync-does-not-mark-library-synced
  ;; Without the `asset-id` check the library would be marked as synced and
  ;; the sync status RPC would be sent, hiding the assets that are still
  ;; pending.
  (let [state (state-with-library)]
    (doseq [[asset-type asset-id] [[:components (uuid/next)]
                                   [:colors (uuid/next)]
                                   [:typographies (uuid/next)]]]
      (t/testing (str asset-type)
        (t/is (false? (dwl/mark-library-synced? file-id library-id asset-id)))
        (t/is (= state
                 (ptk/update (dwl/sync-file file-id library-id asset-type asset-id)
                             state)))))))

(t/deftest whole-library-sync-marks-library-synced
  (let [state  (state-with-library)
        new-st (ptk/update (dwl/sync-file file-id library-id) state)]
    (t/is (true? (dwl/mark-library-synced? file-id library-id nil)))
    (t/is (some? (get-in new-st [:files library-id :synced-at])))))

(t/deftest sync-of-the-library-itself-does-not-mark-it-synced
  ;; The library file is the current file here, so the ids are equal.
  (let [state (assoc (state-with-library) :current-file-id library-id)]
    (t/is (false? (dwl/mark-library-synced? library-id library-id nil)))
    (t/is (= state (ptk/update (dwl/sync-file library-id library-id) state)))))

(t/deftest remove-applied-assets
  (let [component-id (uuid/next)
        color-id     (uuid/next)
        assets       [{:asset-type :component :asset-id component-id}
                      {:asset-type :color :asset-id color-id}]]
    (t/is (= assets (vec (dwl/remove-applied-assets assets #{}))))

    (t/is (= [{:asset-type :color :asset-id color-id}]
             (vec (dwl/remove-applied-assets assets #{[:component component-id]}))))

    ;; Another asset of the same type is kept.
    (t/is (= assets
             (vec (dwl/remove-applied-assets assets #{[:component (uuid/next)]}))))

    (t/is (empty? (dwl/remove-applied-assets assets #{[:component component-id]
                                                      [:color color-id]})))))

(t/deftest sync-file-asset-type-translates-usage-types
  (t/is (= :components (dwl/sync-file-asset-type :component)))
  (t/is (= :colors (dwl/sync-file-asset-type :color)))
  (t/is (= :typographies (dwl/sync-file-asset-type :typography))))
