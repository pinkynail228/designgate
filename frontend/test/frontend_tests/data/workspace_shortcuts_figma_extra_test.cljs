;; This Source Code Form is subject to the terms of the Mozilla Public
;; License, v. 2.0. If a copy of the MPL was not distributed with this
;; file, You can obtain one at http://mozilla.org/MPL/2.0/.
;;
;; Copyright (c) KALEIDOS INC

(ns frontend-tests.data.workspace-shortcuts-figma-extra-test
  (:require
   [app.main.data.shortcuts :as ds]
   [app.main.data.workspace.shortcuts :as shortcuts]
   [cljs.test :as t :include-macros true]))

;; Extra Figma bindings. Most sit next to the key already used for that
;; action, so both stay bound and `:old-command` is asserted too. It is
;; absent for the actions whose previous key does a different action in
;; Figma (see workspace_shortcuts_figma_displace_test), where only the
;; Figma key remains. Expected strings go through the same platform
;; helpers as the table, so they resolve identically on macOS and on
;; Windows/Linux.

(def ^:private expected-bindings
  [{:action :rename
    :new-command (ds/c-mod "r")
    :old-command "alt+n"
    :tooltip (ds/meta "R")}
   {:action :create-component-variant
    :new-command (ds/ca-mod "k")
    :tooltip (ds/meta-alt "K")}
   {:action :detach-component
    :new-command (ds/ca-mod "b")
    :tooltip (ds/meta-alt "B")}
   {:action :toggle-layers
    :new-command (ds/a-mod "1")
    :old-command (ds/a-mod "l")
    :tooltip (ds/alt "1")}
   {:action :toggle-assets
    :new-command (ds/a-mod "2")
    :old-command (ds/a-mod "i")
    :tooltip (ds/alt "2")}])

(defn- commands-of
  "Returns every command string the action binds, as a set."
  [action]
  (let [command (:command (get shortcuts/shortcuts action))]
    (if (vector? command) (set command) #{command})))

(defn- command-owners
  "Returns every action of the workspace table that binds `command`. The
  table is scanned directly because ds/build-command-index keeps only the
  last owner of a repeated command and would hide a collision."
  [command]
  (reduce-kv
   (fn [acc action _sc-def]
     (if (contains? (commands-of action) command)
       (conj acc action)
       acc))
   #{}
   shortcuts/shortcuts))

(t/deftest figma-bindings-are-added-with-the-expected-keys
  (doseq [{:keys [action new-command old-command tooltip]} expected-bindings]
    (t/testing (str action)
      (let [commands (commands-of action)]
        (t/is (contains? commands new-command)
              (str action " should bind " new-command))
        (when old-command
          (t/is (contains? commands old-command)
                (str action " should keep " old-command))))
      (t/is (= #{action} (command-owners new-command))
            (str new-command " should be bound to " action " only"))
      (t/is (= tooltip (:tooltip (get shortcuts/shortcuts action)))
            (str action " should show " tooltip " as its tooltip")))))
