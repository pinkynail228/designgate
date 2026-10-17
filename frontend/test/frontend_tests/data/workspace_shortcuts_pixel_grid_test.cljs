;; This Source Code Form is subject to the terms of the Mozilla Public
;; License, v. 2.0. If a copy of the MPL was not distributed with this
;; file, You can obtain one at http://mozilla.org/MPL/2.0/.
;;
;; Copyright (c) KALEIDOS INC

(ns frontend-tests.data.workspace-shortcuts-pixel-grid-test
  (:require
   [app.main.data.shortcuts :as ds]
   [app.main.data.workspace.shortcuts :as shortcuts]
   [cljs.test :as t :include-macros true]))

;; Figma's pixel-grid and rulers keys. A Figma key must never trigger a
;; different action, so M+Shift+' moves to :snap-pixel-grid while
;; :toggle-snap-guides keeps no key, and Shift+' / Shift+R resolve to the
;; Figma actions. Command strings go through the same `ds` helpers as the
;; table, so the expectations resolve the same way on macOS and on
;; Windows/Linux.

(defn- index
  []
  (ds/build-command-index shortcuts/shortcuts))

(defn- command
  [id]
  (:command (get shortcuts/shortcuts id)))

(defn- tooltip
  [id]
  (:tooltip (get shortcuts/shortcuts id)))

(t/deftest figma-pixel-grid-and-rulers-bind-the-figma-keys
  (let [idx (index)]
    (t/testing "M+Shift+' is Figma's snap to pixel grid, not toggle snap guides"
      (t/is (= :snap-pixel-grid (get idx (ds/c-mod "shift+'"))))
      (t/is (= :snap-pixel-grid (get idx (ds/c-mod "shift+219")))))
    (t/testing "Shift+' is Figma's show pixel grid"
      (t/is (= :show-pixel-grid (get idx "shift+'"))))
    (t/testing "comma stays an alias for snap to pixel grid"
      (t/is (= :snap-pixel-grid (get idx ",")))
      (t/is (contains? (set (command :snap-pixel-grid)) ",")))
    (t/testing "Shift+, keeps the show pixel grid key"
      (t/is (= :show-pixel-grid (get idx "shift+,"))))
    (t/testing "Shift+R is Figma's show rulers"
      (t/is (= :toggle-rulers (get idx "shift+r"))))
    (t/testing "toggle snap guides keeps no key"
      (t/is (empty? (command :toggle-snap-guides)))
      (t/is (not-any? #{:toggle-snap-guides} (vals idx))))))

(t/deftest figma-pixel-grid-and-rulers-command-vectors
  (t/is (= [(ds/c-mod "shift+'") (ds/c-mod "shift+219") ","]
           (command :snap-pixel-grid)))
  (t/is (= ["shift+'" "shift+,"] (command :show-pixel-grid)))
  (t/is (= ["shift+r"] (command :toggle-rulers))))

(t/deftest figma-pixel-grid-and-rulers-tooltips
  (t/is (= (ds/meta-shift "'") (tooltip :snap-pixel-grid)))
  (t/is (= (ds/shift "'") (tooltip :show-pixel-grid)))
  (t/is (= (ds/shift "R") (tooltip :toggle-rulers)))
  (t/is (nil? (tooltip :toggle-snap-guides))))
