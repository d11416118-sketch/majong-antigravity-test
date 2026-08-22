import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

const POSITIONS = ["bottom", "right", "top", "left"];
const CENTER = new THREE.Vector3(0, 0, 0);
const MODEL_FRONT_OFFSET = Math.PI;
const TARGET_MODEL_HEIGHT = 11.8;

const SEAT_LAYOUT = {
    bottom: { x: 0, z: 10, scale: 1.06 },
    right: { x: 11.5, z: 0.3, scale: 0.94 },
    top: { x: 0, z: -9.4, scale: 0.9 },
    left: { x: -11.5, z: 0.3, scale: 0.94 },
};

export class GameCharacters {
    constructor({ canvas, stage }) {
        this.canvas = canvas;
        this.stage = stage;
        this.modelUrl = canvas?.dataset.modelUrl;
        this.renderer = null;
        this.scene = null;
        this.camera = null;
        this.prototype = null;
        this.sharedGeometries = [];
        this.sharedMaterials = [];
        this.players = new Map();
        this.state = null;
        this.myUid = null;
        this.enabled = false;
        this.started = false;
        this.ready = false;
        this.clock = new THREE.Clock();
    }

    setEnabled(enabled) {
        this.enabled = Boolean(enabled);
        if (!this.canvas) {
            return;
        }

        this.canvas.classList.toggle("character-layer-ready", this.enabled && this.ready);
        if (this.enabled) {
            this.start();
            this.resize();
            this.syncPlayers();
        }
    }

    updateState(state, myUid) {
        this.state = state;
        this.myUid = myUid;
        this.syncPlayers();
    }

    start() {
        if (this.started || !this.canvas || !this.stage || !this.modelUrl) {
            return;
        }
        this.started = true;

        try {
            this.renderer = new THREE.WebGLRenderer({
                canvas: this.canvas,
                alpha: true,
                antialias: true,
                powerPreference: "high-performance",
            });
        } catch (error) {
            this.showError(error);
            return;
        }

        this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.6));
        this.renderer.outputColorSpace = THREE.SRGBColorSpace;
        this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
        this.renderer.toneMappingExposure = 1.02;
        this.renderer.shadowMap.enabled = true;

        this.scene = new THREE.Scene();
        this.camera = new THREE.PerspectiveCamera(42, 1, 0.1, 200);
        this.camera.position.set(0, 20, 28);
        this.camera.lookAt(0, 2.5, 0);

        this.addLights();
        this.addSeatMarkers();
        new ResizeObserver(() => this.resize()).observe(this.stage);

        new GLTFLoader().load(
            this.modelUrl,
            (gltf) => this.handleModelLoaded(gltf.scene),
            undefined,
            (error) => this.showError(error)
        );

        this.render();
    }

    addLights() {
        this.scene.add(new THREE.HemisphereLight(0xfff0d0, 0x1f3032, 2.6));

        const key = new THREE.DirectionalLight(0xffd38a, 3.0);
        key.position.set(6, 16, 12);
        key.castShadow = true;
        key.shadow.camera.left = -20;
        key.shadow.camera.right = 20;
        key.shadow.camera.top = 20;
        key.shadow.camera.bottom = -20;
        this.scene.add(key);

        const rim = new THREE.DirectionalLight(0x91c7ff, 1.45);
        rim.position.set(-10, 8, -10);
        this.scene.add(rim);
    }

    addSeatMarkers() {
        Object.entries(SEAT_LAYOUT).forEach(([position, layout]) => {
            const marker = new THREE.Mesh(
                new THREE.RingGeometry(1.4, 1.7, 48),
                new THREE.MeshBasicMaterial({
                    color: 0xd8a23a,
                    transparent: true,
                    opacity: 0.0,
                    depthWrite: false,
                })
            );
            marker.name = `seat-marker-${position}`;
            marker.rotation.x = -Math.PI / 2;
            marker.position.set(layout.x, 0.012, layout.z);
            this.scene.add(marker);
        });
    }

    handleModelLoaded(model) {
        this.prototype = normalizeModel(model);
        collectSharedResources(this.prototype, this.sharedGeometries, this.sharedMaterials);
        this.ready = true;
        this.canvas.classList.toggle("character-layer-ready", this.enabled);
        this.syncPlayers();
    }

    syncPlayers() {
        if (!this.ready || !this.state || !this.state.players || !this.prototype) {
            return;
        }

        const me = this.state.players.find((player) => player.uid === this.myUid);
        const myIdx = me ? me.idx : 0;
        const activeIdx = this.state.current_turn_idx;
        const visibleUids = new Set();

        this.state.players.forEach((player) => {
            const relative = (player.idx - myIdx + 4) % 4;
            const position = POSITIONS[relative];
            const layout = SEAT_LAYOUT[position];
            if (!layout) {
                return;
            }

            const entry = this.getOrCreatePlayer(player.uid);
            visibleUids.add(player.uid);

            entry.group.visible = this.enabled;
            entry.group.position.set(layout.x, 0, layout.z);
            entry.group.scale.setScalar(layout.scale * (player.idx === activeIdx ? 1.09 : 1));
            faceCenter(entry.group);

            entry.marker.position.set(layout.x, 0.014, layout.z);
            entry.marker.material.opacity = player.idx === activeIdx ? 0.58 : 0.16;
            entry.marker.visible = this.enabled;
        });

        this.players.forEach((entry, uid) => {
            if (!visibleUids.has(uid)) {
                entry.group.visible = false;
                entry.marker.visible = false;
            }
        });
    }

    getOrCreatePlayer(uid) {
        if (this.players.has(uid)) {
            return this.players.get(uid);
        }

        const group = cloneModelLightweight(this.prototype);
        group.visible = false;
        this.scene.add(group);

        const marker = new THREE.Mesh(
            new THREE.CircleGeometry(1.8, 48),
            new THREE.MeshBasicMaterial({
                color: 0xd8a23a,
                transparent: true,
                opacity: 0.16,
                depthWrite: false,
            })
        );
        marker.rotation.x = -Math.PI / 2;
        marker.visible = false;
        this.scene.add(marker);

        const entry = { group, marker };
        this.players.set(uid, entry);
        return entry;
    }

    resize() {
        if (!this.renderer || !this.camera || !this.stage) {
            return;
        }

        const width = Math.max(1, this.stage.clientWidth);
        const height = Math.max(1, this.stage.clientHeight);
        this.renderer.setSize(width, height, false);

        this.camera.aspect = width / height;
        this.camera.fov = width < 720 ? 50 : 42;
        this.camera.position.set(0, width < 720 ? 22 : 20, width < 720 ? 32 : 28);
        this.camera.lookAt(0, 2.5, 0);
        this.camera.updateProjectionMatrix();
    }

    render() {
        requestAnimationFrame(() => this.render());
        if (!this.renderer || !this.scene || !this.camera || !this.enabled) {
            return;
        }

        const delta = Math.min(this.clock.getDelta(), 0.05);
        this.players.forEach((entry) => {
            if (!entry.group.visible) {
                return;
            }
            entry.group.position.y = 0.08 + Math.sin(performance.now() * 0.0016 + entry.group.position.x) * 0.04;
            entry.marker.rotation.z += delta * 0.55;
        });

        this.renderer.render(this.scene, this.camera);
    }

    showError(error) {
        this.canvas?.classList.remove("character-layer-ready");
        console.warn("Game character models failed to load", error);
    }
}

function normalizeModel(source) {
    const model = source.clone(true);
    model.traverse((node) => {
        if (!node.isMesh && !node.isSkinnedMesh) {
            return;
        }
        node.castShadow = true;
        node.receiveShadow = true;
        node.frustumCulled = false;
    });

    const initialBox = new THREE.Box3().setFromObject(model);
    const initialSize = initialBox.getSize(new THREE.Vector3());
    const scale = TARGET_MODEL_HEIGHT / Math.max(initialSize.y, 0.001);
    model.scale.setScalar(scale);
    model.updateMatrixWorld(true);

    const box = new THREE.Box3().setFromObject(model);
    const center = box.getCenter(new THREE.Vector3());
    model.position.x -= center.x;
    model.position.z -= center.z;
    model.position.y -= box.min.y;

    const pivot = new THREE.Group();
    pivot.add(model);
    return pivot;
}

/**
 * Collect references to all geometries and materials in the prototype
 * so they aren't garbage-collected while clones reference them.
 */
function collectSharedResources(root, geometries, materials) {
    root.traverse((node) => {
        if (node.geometry) {
            geometries.push(node.geometry);
        }
        if (node.material) {
            const mats = Array.isArray(node.material) ? node.material : [node.material];
            mats.forEach((m) => materials.push(m));
        }
    });
}

/**
 * Lightweight clone that SHARES geometry and materials across instances
 * instead of deep-cloning them. This prevents the GPU memory explosion
 * that was crashing the browser when 4 copies of a 75MB model were created.
 *
 * For SkinnedMesh nodes, we fall back to a regular Mesh so we avoid the
 * broken skeleton-binding issue with SkinnedMesh.clone().
 */
function cloneModelLightweight(prototype) {
    return cloneNodeShared(prototype);
}

function cloneNodeShared(source) {
    let node;

    if (source.isSkinnedMesh) {
        // Convert SkinnedMesh to regular Mesh to avoid skeleton clone issues.
        // The visual appearance is preserved; we just lose bone-driven animation
        // which is acceptable for the static game-table view.
        node = new THREE.Mesh(source.geometry, source.material);
        node.castShadow = source.castShadow;
        node.receiveShadow = source.receiveShadow;
        node.frustumCulled = source.frustumCulled;
        node.visible = source.visible;
        node.renderOrder = source.renderOrder;
    } else if (source.isMesh) {
        node = new THREE.Mesh(source.geometry, source.material);
        node.castShadow = source.castShadow;
        node.receiveShadow = source.receiveShadow;
        node.frustumCulled = source.frustumCulled;
        node.visible = source.visible;
        node.renderOrder = source.renderOrder;
    } else {
        node = new THREE.Group();
        node.visible = source.visible;
    }

    node.name = source.name;
    node.position.copy(source.position);
    node.rotation.copy(source.rotation);
    node.scale.copy(source.scale);
    node.matrix.copy(source.matrix);
    node.matrixAutoUpdate = source.matrixAutoUpdate;

    source.children.forEach((child) => {
        // Skip Bone/Skeleton hierarchy nodes that won't render correctly
        if (child.isBone) {
            return;
        }
        node.add(cloneNodeShared(child));
    });

    return node;
}

function faceCenter(group) {
    group.lookAt(CENTER);
    group.rotateY(MODEL_FRONT_OFFSET);
}
