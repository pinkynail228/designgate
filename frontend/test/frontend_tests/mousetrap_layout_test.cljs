;; This Source Code Form is subject to the terms of the Mozilla Public
;; License, v. 2.0. If a copy of the MPL was not distributed with this
;; file, You can obtain one at http://mozilla.org/MPL/2.0/.
;;
;; Copyright (c) KALEIDOS INC

(ns frontend-tests.mousetrap-layout-test
  "A shortcut bound to a latin key must keep working when the user types with
  a non latin keyboard layout active. The character of the event is not the
  key of the binding there, so mousetrap and the shortcut recorder fall back
  to the physical key (event.code)."
  (:require
   ["@penpot/mousetrap" :refer [Mousetrap]]
   [app.main.ui.shortcuts :as ui-shortcuts]
   [cljs.test :as t :include-macros true]))

(defn- make-element
  "An event target mousetrap can attach its listeners to. A bare `EventTarget`
  is enough for the dispatch, but `stopCallback` reads `dataset`, `className`
  and `tagName` like on a real element, so those fields are defined here."
  [& [tag-name]]
  (let [el (js/EventTarget.)]
    (unchecked-set el "dataset" #js {})
    (unchecked-set el "className" "")
    (unchecked-set el "tagName" (or tag-name "DIV"))
    el))

(defn- make-mousetrap
  [& [el]]
  (let [el (or el (make-element))]
    {:el el
     :mt (Mousetrap. el)}))

(defn- define-prop!
  "A synthetic `Event` cannot carry the keyboard fields through its
  constructor, so they are defined on the instance the same way the browser
  exposes them."
  [ev name value]
  (js/Object.defineProperty ev name #js {:value value :enumerable true})
  ev)

(defn- keyboard-event
  "Builds a real `Event` of `type`, to be sent with `dispatchEvent`. `which`
  mirrors `keyCode`/`charCode` because that is what the legacy mousetrap
  reads."
  [type {:keys [key which code shift?]}]
  (let [ev (js/Event. type #js {:bubbles true :cancelable true})]
    (define-prop! ev "key" key)
    (define-prop! ev "code" code)
    (define-prop! ev "which" which)
    (define-prop! ev "keyCode" which)
    (define-prop! ev "charCode" (if (= type "keypress") which 0))
    (define-prop! ev "shiftKey" (boolean shift?))
    (define-prop! ev "ctrlKey" false)
    (define-prop! ev "altKey" false)
    (define-prop! ev "metaKey" false)
    ev))

(defn- dispatch!
  [el ev]
  (.dispatchEvent el ev))

(defn- recorded-command
  "Runs the recorder on a keyboard event, without dispatching it."
  [props]
  (ui-shortcuts/keyboard-event->mousetrap (keyboard-event "keydown" props)))

(t/deftest plain-letter-shortcut-works-on-a-non-latin-layout
  (let [{:keys [el mt]} (make-mousetrap)
        fired (atom [])]
    (.bind mt "r" (fn [] (swap! fired conj "r")))
    ;; the russian layout produces "к" for the physical key "r"
    (dispatch! el (keyboard-event "keypress" {:key "к" :which 1082 :code "KeyR"}))
    (t/is (= ["r"] @fired)
          "a keypress of a non latin layout falls back to the physical key")))

(t/deftest modifier-shortcut-works-on-a-non-latin-layout
  ;; a combination with modifiers resolves to a keydown binding
  (let [{:keys [el mt]} (make-mousetrap)
        fired (atom [])]
    (.bind mt "shift+p" (fn [] (swap! fired conj "shift+p")))
    ;; chromium and firefox report the latin key code on keydown, so a
    ;; combination already worked before the fallback
    (dispatch! el (keyboard-event "keydown" {:key "П" :which 80 :code "KeyP" :shift? true}))
    (t/is (= ["shift+p"] @fired)
          "a modifier combination keeps working without the fallback"))

  (let [{:keys [el mt]} (make-mousetrap)
        fired (atom [])]
    (.bind mt "shift+p" (fn [] (swap! fired conj "shift+p")))
    ;; when the event only carries the character of the layout
    (dispatch! el (keyboard-event "keydown" {:key "П" :which 1055 :code "KeyP" :shift? true}))
    (t/is (= ["shift+p"] @fired)
          "a keydown without the latin key code falls back to the physical key")))

(t/deftest uppercase-keypress-falls-back-to-the-physical-key
  ;; `_pickBestAction` sends every combination with a modifier to keydown, so a
  ;; shifted binding never reaches the keypress path in practice. This binds
  ;; the upper case character on its own to cover keypress: the russian layout
  ;; produces "П" and the fallback keeps the upper case of the character.
  (let [{:keys [el mt]} (make-mousetrap)
        fired (atom [])]
    (.bind mt "P" (fn [] (swap! fired conj "P")))
    (dispatch! el (keyboard-event "keypress" {:key "П" :which 1055 :code "KeyP" :shift? true}))
    (t/is (= ["P"] @fired)
          "a shifted keypress of a non latin layout keeps the case of the key")))

(t/deftest latin-layout-keeps-the-typed-character
  ;; this guards the scope of the fallback: it must only apply to non ascii
  ;; characters, otherwise an azerty or dvorak user would lose their "a"
  (let [{:keys [el mt]} (make-mousetrap)
        fired (atom [])]
    (.bind mt "a" (fn [] (swap! fired conj "a")))
    (.bind mt "q" (fn [] (swap! fired conj "q")))
    ;; azerty: the key typed as "a" is the physical key "q"
    (dispatch! el (keyboard-event "keypress" {:key "a" :which 97 :code "KeyQ"}))
    (t/is (= ["a"] @fired)
          "the character of a latin layout wins over the physical key")))

(t/deftest editable-targets-are-not-intercepted
  (let [{:keys [el mt]} (make-mousetrap (make-element "INPUT"))
        fired (atom [])]
    (.bind mt "r" (fn [] (swap! fired conj "r")))
    (dispatch! el (keyboard-event "keypress" {:key "к" :which 1082 :code "KeyR"}))
    (t/is (empty? @fired)
          "typing on an editable target is never handled by mousetrap")))

(t/deftest recorder-stores-the-latin-key-of-a-non-latin-layout
  (t/is (= "r" (recorded-command {:key "к" :which 1082 :code "KeyR"}))
        "a shortcut recorded with the russian layout keeps the latin key")
  (t/is (= "shift+p" (recorded-command {:key "П" :which 1055 :code "KeyP" :shift? true})))
  (t/is (= "," (recorded-command {:key "б" :which 1073 :code "Comma"})))
  (t/is (= "a" (recorded-command {:key "a" :which 97 :code "KeyQ"}))
        "a latin layout keeps the recorded character"))
