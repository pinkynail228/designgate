;; This Source Code Form is subject to the terms of the Mozilla Public
;; License, v. 2.0. If a copy of the MPL was not distributed with this
;; file, You can obtain one at http://mozilla.org/MPL/2.0/.
;;
;; Copyright (c) KALEIDOS INC

(ns frontend-tests.data.workspace-shortcuts-figma-test
  (:require
   [app.main.data.shortcuts :as ds]
   [app.main.data.workspace.shortcuts :as shortcuts]
   [cljs.test :as t :include-macros true]))

(defn- commands
  "Returns the command strings declared by a shortcut definition,
  flattening the vector form used for alias bindings."
  [shortcut-def]
  (let [command (:command shortcut-def)]
    (cond
      (vector? command) command
      (string? command) [command]
      :else [])))

(defn- shortcut
  [id]
  (get shortcuts/shortcuts id))

(defn- all-commands
  []
  (->> (vals shortcuts/shortcuts) (mapcat commands) (remove nil?) vec))

(t/deftest draw-frame-uses-figma-keys
  (t/testing "frame is bound to F, keeping A and B as aliases"
    (let [{:keys [command tooltip]} (shortcut :draw-frame)]
      (t/is (= ["f" "a" "b"] command))
      (t/is (= "F" tooltip)))))

(t/deftest draw-ellipse-uses-figma-keys
  (t/testing "ellipse is bound to O, keeping E as an alias"
    (let [{:keys [command tooltip]} (shortcut :draw-ellipse)]
      (t/is (= ["o" "e"] command))
      (t/is (= "O" tooltip)))))

(t/deftest draw-curve-uses-figma-keys
  (t/testing "pencil is bound to Shift+P, keeping Shift+C as an alias"
    (let [{:keys [command tooltip]} (shortcut :draw-curve)]
      (t/is (= ["shift+p" "shift+c"] command))
      (t/is (= (ds/shift "P") tooltip)))))

(t/deftest toggle-focus-mode-uses-shift-f
  (t/testing "focus mode is bound to Shift+F and plain F stays free"
    (let [{:keys [command tooltip]} (shortcut :toggle-focus-mode)]
      (t/is (= "shift+f" command))
      (t/is (not (contains? (set (commands {:command command})) "f")))
      (t/is (= (ds/shift "F") tooltip)))))

;; `:draw-arrow` and `:toggle-lock-size` already shared "shift+l" before this
;; change, so the duplicate check is scoped to the Figma keys instead of the
;; whole table.
(t/deftest figma-keys-bind-once
  (t/testing "each Figma key binds exactly one workspace shortcut"
    (let [figma-keys #{"f" "shift+f" "o" "shift+p"}
          counts     (->> (all-commands)
                          (filter figma-keys)
                          frequencies)]
      (t/is (= {"f" 1 "shift+f" 1 "o" 1 "shift+p" 1} counts)))))
