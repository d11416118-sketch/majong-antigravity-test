import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const homeView = document.getElementById("view-home");
const container = document.getElementById("lobby-character");
const canvas = document.getElementById("lobby-character-canvas");
const selector = document.getElementById("character-options");
const loadingLabel = container?.querySelector(".character-loading");

let started = false;
let viewer = null;
let activeCharacterId = "default";

const characters = container
    ? [
          {
              id: "default",
              label: "預設",
              fullLabel: "預設角色",
              type: "gltf",
              url: container.dataset.modelDefault || container.dataset.modelUrl,
          },
          {
              id: "flair",
              label: "Flair",
              fullLabel: "Flair",
              type: "fbx",
              url: container.dataset.modelFlair,
          },
      ].filter((character) => character.url)
    : [];

if (homeView && container && canvas) {
    initCharacterSelector();

    window.lobbyCharacterViewer = {
        setCharacterId,
        getCharacterId: () => activeCharacterId,
        getOptions: () => characters.map(({ id, label, fullLabel }) => ({ id, label, fullLabel })),
    };

    window.addEventListener("lobby-character-select", (event) => {
        setCharacterId(event.detail?.id);
    });

    const tryStart = () => {
        if (!started && !homeView.classList.contains("hidden")) {
            started = true;
            viewer = startLobbyCharacter();
            setCharacterId(activeCharacterId);
        }
    };

    tryStart();
    new MutationObserver(tryStart).observe(homeView, { attributes: true, attributeFilter: ["class"] });
}

function startLobbyCharacter() {
    let renderer;
    try {
        renderer = new THREE.WebGLRenderer({
            canvas,
            alpha: true,
            antialias: true,
            powerPreference: "high-performance",
        });
    } catch (error) {
        showModelError(error);
        return null;
    }

    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;
    renderer.shadowMap.enabled = true;

    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(34, 1, 0.01, 100);
    const modelGroup = new THREE.Group();
    modelGroup.rotation.y = -0.22;
    scene.add(modelGroup);

    addLights(scene);
    addStage(scene);

    const gltfLoader = new GLTFLoader();
    let activeMixer = null;
    let loadToken = 0;
    let loadedCharacterId = "";

    const loadCharacter = (option) => {
        if (!option?.url || (loadedCharacterId === option.id && !container.classList.contains("model-error"))) {
            return;
        }

        const token = ++loadToken;
        activeMixer = null;
        loadedCharacterId = option.id;
        disposeObject(modelGroup);
        modelGroup.clear();
        modelGroup.rotation.y = -0.22;
        container.dataset.characterId = option.id;
        container.classList.add("model-loading");
        container.classList.remove("model-ready", "model-error");
        if (loadingLabel) {
            loadingLabel.textContent = `載入 ${option.fullLabel || option.label}`;
        }

        const onLoaded = (model, animations = []) => {
            if (token !== loadToken) {
                disposeObject(model);
                return;
            }

            fitModel(model, modelGroup, camera);
            const clip = animations.find((animation) => /idle/i.test(animation.name || "")) || animations[0];
            if (clip) {
                activeMixer = new THREE.AnimationMixer(model);
                activeMixer.clipAction(clip).reset().play();
            }

            container.classList.remove("model-loading", "model-error");
            container.classList.add("model-ready");
        };

        const onError = (error) => {
            if (token === loadToken) {
                loadedCharacterId = "";
                showModelError(error);
            }
        };

        if (option.type === "fbx") {
            loadFbxModel(option.url).then((model) => onLoaded(model, model.animations || [])).catch(onError);
        } else {
            gltfLoader.load(option.url, (gltf) => onLoaded(gltf.scene, gltf.animations || []), undefined, onError);
        }
    };

    bindDragRotation(modelGroup);

    const resize = () => {
        const width = Math.max(1, container.clientWidth);
        const height = Math.max(1, container.clientHeight);
        renderer.setSize(width, height, false);
        camera.aspect = width / height;
        camera.updateProjectionMatrix();
    };

    new ResizeObserver(resize).observe(container);
    resize();

    const clock = new THREE.Clock();
    const render = () => {
        const delta = Math.min(clock.getDelta(), 0.05);
        if (activeMixer) {
            activeMixer.update(delta);
        }
        if (!container.classList.contains("dragging-model")) {
            modelGroup.rotation.y += delta * 0.22;
        }
        renderer.render(scene, camera);
        requestAnimationFrame(render);
    };
    render();

    return { loadCharacter };
}

async function loadFbxModel(url) {
    const { FBXLoader } = await import("three/addons/loaders/FBXLoader.js");
    return new Promise((resolve, reject) => {
        new FBXLoader().load(url, resolve, undefined, reject);
    });
}

function initCharacterSelector() {
    if (!selector || !characters.length) {
        return;
    }

    selector.innerHTML = "";
    characters.forEach((character) => {
        const button = document.createElement("button");
        button.type = "button";
        button.dataset.characterId = character.id;
        button.textContent = character.label;
        button.setAttribute("role", "radio");
        button.setAttribute("aria-checked", "false");
        button.addEventListener("click", () => {
            setCharacterId(character.id, { persist: true });
        });
        selector.appendChild(button);
    });
    updateCharacterSelector();
}

function setCharacterId(characterId, options = {}) {
    const character = characters.find((item) => item.id === characterId) || characters[0];
    if (!character) {
        return;
    }

    const changed = activeCharacterId !== character.id;
    activeCharacterId = character.id;
    updateCharacterSelector();
    if (viewer) {
        viewer.loadCharacter(character);
    }
    if (options.persist && changed) {
        window.dispatchEvent(new CustomEvent("lobby-character-change", { detail: { id: character.id } }));
    }
}

function updateCharacterSelector() {
    if (!selector) {
        return;
    }

    selector.querySelectorAll("button").forEach((button) => {
        const active = button.dataset.characterId === activeCharacterId;
        button.classList.toggle("active", active);
        button.setAttribute("aria-checked", active ? "true" : "false");
    });
}

function addLights(scene) {
    scene.add(new THREE.HemisphereLight(0xfff4d6, 0x253436, 2.8));

    const key = new THREE.DirectionalLight(0xffdf9a, 3.2);
    key.position.set(2.5, 4.5, 3.5);
    key.castShadow = true;
    scene.add(key);

    const fill = new THREE.DirectionalLight(0x91c7ff, 1.15);
    fill.position.set(-3.5, 2.2, -2.8);
    scene.add(fill);
}

function addStage(scene) {
    const floor = new THREE.Mesh(
        new THREE.CircleGeometry(1.75, 72),
        new THREE.MeshStandardMaterial({
            color: 0x2b332f,
            roughness: 0.9,
            metalness: 0.05,
            transparent: true,
            opacity: 0.48,
        })
    );
    floor.rotation.x = -Math.PI / 2;
    floor.position.y = -1.55;
    floor.receiveShadow = true;
    scene.add(floor);
}

function fitModel(model, group, camera) {
    model.traverse((node) => {
        if (!node.isMesh && !node.isSkinnedMesh) {
            return;
        }
        node.castShadow = true;
        node.receiveShadow = true;
        if (node.material) {
            node.material.needsUpdate = true;
        }
    });

    const box = new THREE.Box3().setFromObject(model);
    const size = box.getSize(new THREE.Vector3());
    const center = box.getCenter(new THREE.Vector3());
    const maxDimension = Math.max(size.x * 0.82, size.y, size.z * 0.82, 0.001);
    const scale = 3.15 / maxDimension;

    model.scale.setScalar(scale);
    model.position.copy(center).multiplyScalar(-scale);
    group.add(model);

    const fittedHeight = size.y * scale;
    camera.position.set(0, fittedHeight * 0.08, 4.15);
    camera.lookAt(0, fittedHeight * 0.04, 0);
}

function bindDragRotation(group) {
    let dragging = false;
    let lastX = 0;

    const stopDrag = () => {
        dragging = false;
        container.classList.remove("dragging-model");
    };

    canvas.addEventListener("pointerdown", (event) => {
        dragging = true;
        lastX = event.clientX;
        container.classList.add("dragging-model");
        canvas.setPointerCapture(event.pointerId);
    });

    canvas.addEventListener("pointermove", (event) => {
        if (!dragging) {
            return;
        }
        const deltaX = event.clientX - lastX;
        lastX = event.clientX;
        group.rotation.y += deltaX * 0.012;
    });

    canvas.addEventListener("pointerup", stopDrag);
    canvas.addEventListener("pointercancel", stopDrag);
    canvas.addEventListener("pointerleave", stopDrag);
}

function disposeObject(root) {
    root.traverse((node) => {
        if (node.geometry) {
            node.geometry.dispose();
        }
        const materials = Array.isArray(node.material) ? node.material : [node.material];
        materials.filter(Boolean).forEach((material) => {
            Object.values(material).forEach((value) => {
                if (value?.isTexture) {
                    value.dispose();
                }
            });
            material.dispose?.();
        });
    });
}

function showModelError(error) {
    container.classList.remove("model-loading", "model-ready");
    container.classList.add("model-error");
    if (loadingLabel) {
        loadingLabel.textContent = "載入失敗";
    }
    console.warn("Lobby character model failed to load", error);
}
